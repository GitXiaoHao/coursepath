"""阶段 2 环境 spike：验证 Dify 能真正调用自研 campus-kb。

链路：Dify 控制台 API → 外部知识库(External Knowledge API) → SSRF Proxy → campus-kb /retrieval

为什么必须显式验证：Dify 所有出站 HTTP 都经过 Squid 型 SSRF 代理，容器名
`campus-kb` 属于私有网段，默认会被 `http_access deny to_private_networks` 拦掉。
只有把 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=campus-kb` 写进 .env 才放行，本脚本
就是这条链路的可复现证据。

用法（Dify 已启动、campus-kb 已 healthy）：
    python deploy/spike_dify.py
    python deploy/spike_dify.py --query "编译原理的先修要求是什么"

退出码：0 全通；1 有断言失败。
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("DIFY_BASE_URL", "http://localhost")
EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@campus-kb.local")
PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "CampusKb2026!")

ENV_FILE = Path(__file__).parent / "dify" / "docker" / ".env"
API_NAME = "campus-kb-connector"
DATASET_NAME = "课径 · 培养方案知识库"
#: 外部知识库的检索参数。**必须写进 dataset.retrieval_model**：工作流里的外部
#: 数据集走 `dataset_retrieval.py:1166`，直接读库里的 `dataset.retrieval_model`；
#: `dataset.retrieval_model`；若是 None，`external_retrieval_parameters.get(...)`
#: 会抛 `'NoneType' object has no attribute 'get'`，而该异常被 `skip_on_error`
#: 吞掉，表现为"检索静默返回 0 条、回答为空"。这是 1.17.1 的坑，已实测踩到。
RETRIEVAL_MODEL = {"top_k": 4, "score_threshold": 0.0, "score_threshold_enabled": False}


def read_env(key: str, default: str = "") -> str:
    """从 deploy/dify/docker/.env 读取配置，避免把密钥再写一份到脚本里。"""
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return default


class Dify:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf = ""

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        url = BASE + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.csrf:
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req, timeout=120) as resp:
                body = resp.read().decode()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def login(self) -> None:
        # Dify 控制台要求密码先 base64，成功后靠 cookie 维持会话
        pw = base64.b64encode(PASSWORD.encode()).decode()
        status, body = self._request(
            "POST",
            "/console/api/login",
            {"email": EMAIL, "password": pw, "remember_me": True},
        )
        if status != 200:
            raise SystemExit(f"登录失败 {status}: {body}")
        for cookie in self.jar:
            if cookie.name == "csrf_token":
                self.csrf = cookie.value
        print(f"[1/4] 登录成功 {EMAIL}（csrf {'已获取' if self.csrf else '缺失'}）")

    def get(self, path: str, query: str = "") -> tuple[int, object]:
        return self._request("GET", path + query)

    def post(self, path: str, payload: dict) -> tuple[int, object]:
        return self._request("POST", path, payload)


def find_api_id(client: Dify) -> str | None:
    status, body = client.get("/console/api/datasets/external-knowledge-api", "?page=1&limit=100")
    if status != 200 or not isinstance(body, dict):
        return None
    for item in body.get("data", []):
        if item.get("name") == API_NAME:
            return item["id"]
    return None


def find_dataset_id(client: Dify) -> str | None:
    status, body = client.get("/console/api/datasets", "?page=1&limit=100")
    if status != 200 or not isinstance(body, dict):
        return None
    for item in body.get("data", []):
        if item.get("name") == DATASET_NAME:
            return item["id"]
    return None


def ensure_external_dataset(client: Dify, endpoint: str = "http://campus-kb:8080") -> str:
    """确保「外部知识库 API」与「外部知识库」都已登记，返回 dataset id（幂等）。

    endpoint 不带 ``/retrieval``——Dify 调用时会自己拼上去，写全了会变成
    ``.../retrieval/retrieval``。
    """
    kb_key = read_env("CAMPUS_KB_API_KEY")
    if not kb_key:
        raise SystemExit("deploy/dify/docker/.env 里没有 CAMPUS_KB_API_KEY")

    api_id = find_api_id(client)
    if api_id:
        print(f"[2/4] 复用已登记的外部知识库 API {api_id}")
    else:
        status, body = client.post(
            "/console/api/datasets/external-knowledge-api",
            {"name": API_NAME, "settings": {"endpoint": endpoint, "api_key": kb_key}},
        )
        if status not in (200, 201):
            raise SystemExit(f"[2/4] 登记失败 {status}: {body}")
        api_id = body["id"]
        print(f"[2/4] 登记外部知识库 API {api_id} -> {endpoint}")

    dataset_id = find_dataset_id(client)
    if dataset_id:
        print(f"[3/4] 复用已绑定的外部知识库 {dataset_id}")
        # 修复历史数据：早期创建时没带 external_retrieval_model，工作流检索会静默失败。
        # 注意 `_update_external_dataset` 同时要求 external_knowledge_id 与
        # external_knowledge_api_id 非空，只给检索参数会被 400 拒掉。
        status, body = client._request(
            "PATCH",
            f"/console/api/datasets/{dataset_id}",
            {
                "external_retrieval_model": RETRIEVAL_MODEL,
                "external_knowledge_api_id": api_id,
                "external_knowledge_id": read_env("CAMPUS_KB_KNOWLEDGE_ID", "campus-kb"),
            },
        )
        if status != 200:
            print(f"[3/4] 修补检索参数失败 {status}: {body}")
        return dataset_id

    status, body = client.post(
        "/console/api/datasets/external",
        {
            "name": DATASET_NAME,
            "description": "自研混合检索服务，含培养方案表格与先修关系",
            "external_knowledge_api_id": api_id,
            "external_knowledge_id": read_env("CAMPUS_KB_KNOWLEDGE_ID", "campus-kb"),
            "external_retrieval_model": RETRIEVAL_MODEL,
        },
    )
    if status not in (200, 201):
        raise SystemExit(f"[3/4] 绑定失败 {status}: {body}")
    dataset_id = body["id"]
    print(f"[3/4] 绑定外部知识库 {dataset_id}")
    return dataset_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="编译原理的先修要求是什么")
    parser.add_argument("--endpoint", default="http://campus-kb:8080")
    args = parser.parse_args()

    client = Dify()
    client.login()
    dataset_id = ensure_external_dataset(client, args.endpoint)

    # 第 4 步：命中测试——这一步才真正经 SSRF Proxy 打到 campus-kb
    status, body = client.post(
        f"/console/api/datasets/{dataset_id}/external-hit-testing",
        {"query": args.query, "external_retrieval_model": {"top_k": 3, "score_threshold": 0.0}},
    )
    if status != 200:
        print(f"[4/4] 命中测试失败 {status}: {body}")
        return 1

    records = body.get("records", []) if isinstance(body, dict) else []
    print(f"[4/4] 命中测试通过，返回 {len(records)} 条（query={args.query}）")
    for i, rec in enumerate(records, 1):
        meta = rec.get("metadata") or {}
        print(
            f"      {i}. score={rec.get('score'):.4f} "
            f"{rec.get('title', '')} | doc={meta.get('doc_id')} p{meta.get('page')}"
        )

    if not records:
        print("断言失败：外部知识库返回 0 条，检查 SSRF 白名单与 campus-kb 日志")
        return 1
    if not isinstance(records[0].get("metadata"), dict):
        print("断言失败：metadata 不是对象，Dify 检索管线会报错")
        return 1
    print("SPIKE PASS：Dify -> SSRF Proxy -> campus-kb 链路打通")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""把本地打好的 .difypkg 安装进本机 Dify，并验证工具可被工作流发现。

用法（在仓库根执行，先启动 Dify）：
    .\\.tools\\plugin\\dify-plugin.exe plugin package .\\dify-plugin\\course-audit -o .\\dify-plugin\\dist\\course_audit-0.1.0.difypkg
    python deploy\\install_plugin.py
    python deploy\\install_plugin.py --uninstall

设计说明：
- 控制台登录密码必须 base64（明文会被判 "Invalid encrypted data"）。
- 认证靠 cookie，所有 POST 必须带 X-CSRF-Token（取自 csrf_token cookie）。
- 上传与安装是两个接口：upload/pkg 解码出 plugin_unique_identifier，
  install/pkg 传入该 identifier 才真正安装。
"""

from __future__ import annotations
import os

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost"
def _require_password() -> str:
    if not PASSWORD:
        raise SystemExit(
            "请先设置环境变量 DIFY_ADMIN_PASSWORD（本机 Dify 管理员密码），"
            "例如：$env:DIFY_ADMIN_PASSWORD = '<你的密码>'")
    return PASSWORD


EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@campus-kb.local")
PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "")
PKG = Path(__file__).resolve().parent.parent / "dify-plugin" / "dist" / "course_audit-0.2.1.difypkg"
PLUGIN_NAME = "course-audit"


class Console:
    def __init__(self, base: str = BASE) -> None:
        self.client = httpx.Client(base_url=base, timeout=60.0, follow_redirects=True)

    def _csrf(self) -> str:
        token = self.client.cookies.get("csrf_token")
        if not token:
            raise RuntimeError("缺少 csrf_token cookie，登录可能失败")
        return token

    def post(self, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = self._csrf()
        return self.client.post(path, headers=headers, **kwargs)

    def get(self, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = self._csrf()
        return self.client.get(path, headers=headers, **kwargs)

    def login(self) -> None:
        resp = self.client.post(
            "/console/api/login",
            json={
                "email": EMAIL,
                "password": base64.b64encode(_require_password().encode()).decode(),
                "remember_me": True,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"登录失败 {resp.status_code}: {resp.text[:300]}")


def upload(client: Console, pkg: Path) -> str:
    content = pkg.read_bytes()
    resp = client.post(
        "/console/api/workspaces/current/plugin/upload/pkg",
        files={"pkg": (pkg.name, content, "application/octet-stream")},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"上传失败 {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    identifier = data.get("unique_identifier") or data.get("plugin_unique_identifier")
    if not identifier:
        raise RuntimeError(f"上传响应里没有 unique_identifier: {json.dumps(data, ensure_ascii=False)[:500]}")
    return identifier


def install(client: Console, identifier: str) -> None:
    resp = client.post(
        "/console/api/workspaces/current/plugin/install/pkg",
        json={"plugin_unique_identifiers": [identifier]},
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"安装失败 {resp.status_code}: {resp.text[:500]}")
    payload = resp.json()
    print(f"安装任务已提交: {json.dumps(payload, ensure_ascii=False)[:300]}")
    # 任务状态接口的字段结构随版本变化，直接轮询"已装列表"更稳。
    deadline = time.time() + 240
    while time.time() < deadline:
        plugins = list_plugins(client)
        hit = [p for p in plugins if PLUGIN_NAME in json.dumps(p, ensure_ascii=False)]
        if hit and any(p.get("install_status") in (None, "installed") for p in hit):
            return
        failed = [p for p in plugins if p.get("install_status") == "install_failed"]
        if failed:
            raise RuntimeError(f"插件安装失败: {json.dumps(failed[0], ensure_ascii=False)[:600]}")
        time.sleep(5)
    raise RuntimeError("等待插件安装超时（240s），请查看 plugin_daemon 日志")


def list_plugins(client: Console) -> list[dict]:
    resp = client.get("/console/api/workspaces/current/plugin/list")
    resp.raise_for_status()
    body = resp.json()
    return body.get("plugins", body if isinstance(body, list) else [])


def verify(client: Console) -> bool:
    plugins = list_plugins(client)
    target = [p for p in plugins if PLUGIN_NAME in json.dumps(p, ensure_ascii=False)]
    if not target:
        print(f"未在已装插件里找到 {PLUGIN_NAME}，当前共 {len(plugins)} 个")
        return False
    plugin = target[0]
    print(
        "已安装: "
        + json.dumps(
            {
                k: plugin.get(k)
                for k in ("plugin_id", "name", "version", "install_status", "installation_id")
                if plugin.get(k) is not None
            },
            ensure_ascii=False,
        )
    )
    # 工具清单用于核对节点可选工具
    tools = client.get(f"/console/api/workspaces/current/tool-provider/{plugin.get('provider_id') or 'course-audit'}/tools")
    if tools.status_code == 200:
        names = [t.get("name") for t in tools.json()]
        print(f"工具清单: {names}")
    return True


def uninstall(client: Console) -> None:
    plugins = list_plugins(client)
    hit = [p for p in plugins if PLUGIN_NAME in json.dumps(p, ensure_ascii=False)]
    for plugin in hit:
        resp = client.post(
            "/console/api/workspaces/current/plugin/uninstall",
            json={"plugin_unique_identifier": plugin["plugin_unique_identifier"]},
        )
        print(f"卸载 {plugin.get('name')} → {resp.status_code}")
    if not hit:
        print("没有可卸载的同名插件")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pkg", default=str(PKG))
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    client = Console()
    client.login()
    print("登录成功")

    if args.uninstall:
        uninstall(client)
        return 0

    pkg = Path(args.pkg)
    if not pkg.exists():
        print(f"找不到安装包 {pkg}", file=sys.stderr)
        return 1
    identifier = upload(client, pkg)
    print(f"上传成功 unique_identifier={identifier}")
    install(client, identifier)
    ok = verify(client)
    print("PLUGIN PASS：插件安装成功且工具可用" if ok else "PLUGIN FAIL：插件未出现在已装列表")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - CLI 入口
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

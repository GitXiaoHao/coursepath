"""生成并导入「课径」Chatflow 工作流 DSL。

为什么用脚本生成而不是手写 YAML：节点的 ``data`` 字段是一份有严格必填项的
pydantic 模型（``graphon.nodes.*.entities``），手写极易漏字段。这里用结构化
代码拼装，产物 ``course-audit.chatflow.yml`` 才是提交/评审要看的那份 DSL。

工作流（全部在 Dify 应用层完成，不碰源码）：

    start ──► knowledge-retrieval ──► code(整理条款) ──► if-else ─┬─ true   ──► http /prereq ──► answer
                                                                  ├─ clause ──► answer（仅条款摘录）
                                                                  └─ false  ──► http /audit  ──► answer

- knowledge-retrieval 走「外部知识库」，指向自研 campus-kb 的 /retrieval
- code 节点把检索命中的数组整理成可读条款清单（answer 节点取不到数组字段）
- http-request 调自研 /audit、/prereq 做确定性核算（代码节点无外网，必须走 HTTP 节点）
- if-else 用关键词把"先修类""条款类""学分类"问题分流（无模型依赖，可解释）

用法：
    python dify-workflow/build_chatflow.py            # 只生成 DSL 文件
    python dify-workflow/build_chatflow.py --import   # 生成 + 导入 + 发布 + 冒烟测试
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))

OUT_FILE = Path(__file__).parent / "course-audit.chatflow.yml"

#: 先修类问题的关键词（if-else 的 or 条件），命中任一即走 /prereq 分支
PREREQ_KEYWORDS = ["先修", "前置", "能不能选", "可以选", "能否选"]
#: 条款类问题的关键词（转专业、认定、免修等），走"检索条款"分支
CLAUSE_KEYWORDS = ["转专业", "学分认定", "免修", "重修", "替代", "抵", "条例", "规定", "政策"]

#: 知识检索结果 → 可读条款清单的代码节点脚本。
#: 为什么用代码节点：Dify 的 answer 节点只能拼字符串，取不到数组里的字段；
#: 代码节点（dify-sandbox）没有外网，但做纯文本整理完全够用。
CITE_CODE = '''
def main(docs: list) -> dict:
    if not docs:
        return {"text": "\\n> 未在知识库中检索到相关条款，请补充课程代码或换个问法。"}
    lines = ["", "**相关条款（检索命中，可逐条核对）**"]
    for i, doc in enumerate(docs[:3], 1):
        meta = doc.get("metadata") or {}
        doc_meta = meta.get("doc_metadata") or {}
        page = doc_meta.get("page") or ""
        row = doc_meta.get("table_row") or ""
        loc = "第 {} 页".format(page) if page else ""
        if row:
            loc = "{} 第 {} 行".format(loc, row) if loc else "第 {} 行".format(row)
        name = (doc.get("title") or doc_meta.get("title") or "条款").strip()
        content = (doc.get("content") or "").strip().replace("\\n", " ")
        if len(content) > 100:
            content = content[:100] + "…"
        try:
            score = float(meta.get("score") or doc_meta.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        lines.append("{}. {}（{}，匹配度 {:.2f}）：{}".format(i, name, loc or "位置未登记", score, content))
    return {"text": "\\n".join(lines)}
'''

KB_BASE = "http://campus-kb:8080"


def node(node_id: str, data: dict, x: int, y: int, width: int = 244, height: int = 90) -> dict:
    return {
        "data": {"desc": "", "selected": False, **data},
        "height": height,
        "id": node_id,
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": width,
    }


def edge(src: str, dst: str, src_type: str, dst_type: str, handle: str = "source") -> dict:
    return {
        "data": {"isInIteration": False, "sourceType": src_type, "targetType": dst_type},
        "id": f"{src}-{handle}-{dst}",
        "source": src,
        "sourceHandle": handle,
        "target": dst,
        "targetHandle": "target",
    }


def http_node(node_id: str, title: str, path: str, body: dict, api_key: str, x: int, y: int) -> dict:
    """HTTP 请求节点：统一走 api-key 鉴权，body 为单个 JSON 文本字段。"""
    return node(
        node_id,
        {
            "type": "http-request",
            "title": title,
            "method": "post",
            "url": f"{KB_BASE}{path}",
            "authorization": {"type": "api-key", "config": {"type": "bearer", "api_key": api_key, "header": ""}},
            "headers": "Content-Type: application/json",
            "params": "",
            # ``BodyData.file`` 是必填字段（Sequence[str]），即便 json 体也要给空列表
            "body": {
                "type": "json",
                "data": [{"key": "", "type": "text", "value": json.dumps(body, ensure_ascii=False), "file": []}],
            },
            "timeout": {"connect": 10, "read": 60, "write": 30},
            "ssl_verify": True,
            "retry_config": {"max_retries": 2, "retry_interval": 500, "retry_enabled": True},
        },
        x,
        y,
        width=300,
    )


def build(api_key: str, dataset_id: str) -> dict:
    # 注意：Chatflow 的用户问题由系统变量 sys.query 承载，**不能**在开始节点里
    # 再声明一个叫 query 的输入变量——那样服务 API 会要求 inputs.query 而报
    # "query is required in input form"。开始节点只放业务自定义输入。
    prereq_conditions = [
        {
            "variable_selector": ["sys", "query"],
            "comparison_operator": "contains",
            "value": keyword,
        }
        for keyword in PREREQ_KEYWORDS
    ]
    clause_conditions = [
        {
            "variable_selector": ["sys", "query"],
            "comparison_operator": "contains",
            "value": keyword,
        }
        for keyword in CLAUSE_KEYWORDS
    ]

    nodes = [
        node(
            "start",
            {
                "type": "start",
                "title": "开始",
                "variables": [
                    {
                        "variable": "completed",
                        "label": "已修课程代码（可选，逗号分隔，如 CS101,CS201）",
                        "type": "text-input",
                        "required": False,
                        "max_length": 512,
                        "options": [],
                    },
                ],
            },
            60,
            260,
            width=280,
            height=120,
        ),
        node(
            "kb",
            {
                "type": "knowledge-retrieval",
                "title": "培养方案检索（外部知识库）",
                "query_variable_selector": ["sys", "query"],
                "dataset_ids": [dataset_id],
                "retrieval_mode": "multiple",
                "multiple_retrieval_config": {
                    "top_k": 4,
                    "score_threshold": None,
                    "reranking_enable": False,
                    "reranking_mode": "reranking_model",
                    "reranking_model": None,
                    "weights": None,
                },
                "metadata_filtering_mode": "disabled",
                "vision": {"enabled": False},
            },
            400,
            260,
            width=300,
            height=110,
        ),
        node(
            "cite",
            {
                "type": "code",
                "title": "整理检索条款",
                "code_language": "python3",
                "code": CITE_CODE,
                "variables": [{"variable": "docs", "value_selector": ["kb", "result"]}],
                "outputs": {"text": {"type": "string", "children": None}},
            },
            760,
            260,
            width=300,
            height=120,
        ),
        node(
            "branch",
            {
                "type": "if-else",
                "title": "问题类型分流",
                "logical_operator": "or",
                "cases": [
                    {
                        "case_id": "true",
                        "logical_operator": "or",
                        "conditions": [dict(c) for c in prereq_conditions],
                    },
                    {
                        "case_id": "clause",
                        "logical_operator": "or",
                        "conditions": [dict(c) for c in clause_conditions],
                    },
                ],
            },
            1080,
            260,
            width=280,
            height=150,
        ),
        http_node(
            "prereq",
            "先修链核算 /prereq",
            "/prereq",
            {"course_name": "{{#sys.query#}}", "completed_text": "{{#start.completed#}}", "format": "text"},
            api_key,
            1420,
            60,
        ),
        http_node(
            "audit",
            "学分核算 /audit",
            "/audit",
            {"completed_text": "{{#start.completed#}}", "format": "text"},
            api_key,
            1420,
            260,
        ),
        node(
            "answer_clause",
            {
                "type": "answer",
                "title": "回答（条款）",
                "answer": (
                    "### 条款速览\n"
                    "这个问题不属于可核算类型（学分缺口/先修关系），先返回知识库里最相关的原文条款。\n"
                    "{{#cite.text#}}\n\n"
                    "> 条款为原文摘录，未做核算；涉及学分认定以教务最终认定为准。"
                ),
            },
            1420,
            460,
            width=300,
            height=140,
        ),
        node(
            "answer_prereq",
            {
                "type": "answer",
                "title": "回答（先修）",
                # 服务端用 format=text 直接回 Markdown 摘要：HTTP 节点的 JSON
                # 响应体在 Dify 里是字符串，取不到 body.summary 子字段
                "answer": (
                    "{{#prereq.body#}}\n"
                    "{{#cite.text#}}\n\n"
                    "> 先修判定来自培养方案/教学大纲登记的先修关系，逐条可回溯；"
                    "计算过程与条款均由确定性代码给出，非模型生成。"
                ),
            },
            1760,
            60,
            width=300,
            height=140,
        ),
        node(
            "answer_audit",
            {
                "type": "answer",
                "title": "回答（学分）",
                "answer": (
                    "{{#audit.body#}}\n"
                    "{{#cite.text#}}\n\n"
                    "> 学分按模块分别核算、超额不冲抵；未报学分的课程按培养方案登记值计。"
                    "计算过程与条款均由确定性代码给出，非模型生成。"
                ),
            },
            1760,
            260,
            width=300,
            height=140,
        ),
    ]

    edges = [
        edge("start", "kb", "start", "knowledge-retrieval"),
        edge("kb", "cite", "knowledge-retrieval", "code"),
        edge("cite", "branch", "code", "if-else"),
        edge("branch", "prereq", "if-else", "http-request", handle="true"),
        edge("branch", "audit", "if-else", "http-request", handle="false"),
        edge("branch", "answer_clause", "if-else", "answer", handle="clause"),
        edge("prereq", "answer_prereq", "http-request", "answer"),
        edge("audit", "answer_audit", "http-request", "answer"),
    ]

    return {
        "app": {
            "description": "「课径」：把培养方案条款变成可核算的问答，每题都给引用与计算过程。",
            "icon": "📘",
            "icon_background": "#E0F2FE",
            "mode": "advanced-chat",
            "name": "课径 · 可核算的培养方案问答",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": "app",
        "version": "0.3.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {
                    "allowed_file_extensions": [],
                    "allowed_file_types": [],
                    "allowed_file_size": 15,
                    "enabled": False,
                },
                "opening_statement": (
                    "你好，我是「课径」。可以问我：\n"
                    "1. 我修完这些课还差多少学分？（先在下方填已修课程代码，如 CS101,CS201）\n"
                    "2. 编译原理的先修要求是什么？我能选吗？\n"
                    "每条回答都会给出依据条款与计算步骤。"
                ),
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                    "毕业还差多少学分？",
                    "编译原理的先修要求是什么？",
                ],
                "suggested_questions_after_answer": {"enabled": True},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {"edges": edges, "nodes": nodes},
        },
    }


APP_NAME = "课径 · 可核算的培养方案问答"


def delete_existing_apps(client, name: str = APP_NAME) -> None:
    """重跑时先删同名应用，避免控制台堆一堆重复应用。"""
    status, body = client.get("/console/api/apps", "?page=1&limit=100")
    if status != 200 or not isinstance(body, dict):
        return
    for item in body.get("data", []):
        if item.get("name") == name:
            client._request("DELETE", f"/console/api/apps/{item['id']}")
            print(f"已删除同名旧应用 {item['id']}")


def import_app(client, dsl_text: str) -> str:
    status, body = client.post(
        "/console/api/apps/imports",
        {"mode": "yaml-content", "yaml_content": dsl_text},
    )
    if status not in (200, 201) or not isinstance(body, dict):
        raise SystemExit(f"导入失败 {status}: {body}")
    # Dify 会在导入时把 DSL 从 0.3.0 迁移到当前版本（1.17.x 为 0.7.0），
    # 迁移成功也可能返回 completed-with-warnings，因此只拒绝明确失败的状态。
    status_text = str(body.get("status") or "")
    if not status_text.startswith("completed"):
        raise SystemExit(f"导入未完成：{body}")
    if body.get("error"):
        raise SystemExit(f"导入报错：{body.get('error')}")
    app_id = body.get("app_id") or (body.get("app") or {}).get("id")
    if not app_id:
        raise SystemExit(f"导入结果里没有 app_id：{body}")
    print(f"导入成功 app_id={app_id}")
    return app_id


def publish_app(client, app_id: str) -> None:
    status, body = client.post(
        f"/console/api/apps/{app_id}/workflows/publish",
        {"marked_name": "课径 v0.1", "marked_comment": "检索+分流+核算三件套"},
    )
    if status not in (200, 201):
        raise SystemExit(f"发布失败 {status}: {body}")
    print("已发布工作流")


def create_api_key(client, app_id: str) -> str:
    status, body = client.post(f"/console/api/apps/{app_id}/api-keys", {})
    if status not in (200, 201) or not isinstance(body, dict) or not body.get("token"):
        raise SystemExit(f"创建 API Key 失败 {status}: {body}")
    return body["token"]


def smoke(api_key: str, queries: list[tuple[str, str]]) -> int:
    """走服务 API（评审/集成方真正会用的入口）打两问，验证整条链路。"""
    failures = 0
    for i, (query, completed) in enumerate(queries, 1):
        payload = {
            "inputs": {"completed": completed},
            "query": query,
            "response_mode": "blocking",
            "user": "spike",
        }
        req = urllib.request.Request(
            "http://localhost/v1/chat-messages",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            print(f"[{i}] 调用失败 {exc.code}: {exc.read().decode()[:500]}")
            failures += 1
            continue
        answer = (body.get("answer") or "").strip()
        print(f"\n[{i}] Q={query} completed={completed!r}")
        print(f"    A={answer[:600]}")
        if not answer:
            print("    断言失败：回答为空")
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import", dest="do_import", action="store_true", help="导入到本机 Dify 并冒烟测试")
    parser.add_argument("--api-key", default=None, help="campus-kb 的 API Key（默认读 deploy/dify/docker/.env）")
    parser.add_argument("--dataset-id", default=None, help="外部知识库 dataset id（默认自动登记/复用）")
    args = parser.parse_args()

    from spike_dify import Dify, ensure_external_dataset, read_env  # 复用已验收的客户端

    api_key = args.api_key or read_env("CAMPUS_KB_API_KEY")
    if not api_key:
        print("缺少 campus-kb API Key：先用 deploy/bootstrap.ps1 生成 .env")
        return 1

    dataset_id = args.dataset_id
    client = None
    if not dataset_id:
        if not args.do_import:
            print("生成 DSL 需要 --dataset-id（或加 --import 让脚本自动登记外部知识库）")
            return 1
        client = Dify()
        client.login()
        dataset_id = ensure_external_dataset(client)
        print(f"外部知识库 dataset_id = {dataset_id}")

    dsl = build(api_key, dataset_id)
    dsl_text = yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False)
    # 落盘的 DSL 把密钥替换成占位符：这份文件要进仓库/进提交包，
    # 明文密钥会被判"泄露凭据"。导入时仍用内存里的真实值。
    OUT_FILE.write_text(dsl_text.replace(api_key, "${CAMPUS_KB_API_KEY}"), encoding="utf-8")
    print(f"已生成 {OUT_FILE.relative_to(ROOT)}（密钥已替换为 ${{CAMPUS_KB_API_KEY}}）")

    if not args.do_import:
        return 0

    client = client or Dify()
    if not getattr(client, "csrf", ""):
        client.login()
    delete_existing_apps(client)
    app_id = import_app(client, dsl_text)
    publish_app(client, app_id)
    app_key = create_api_key(client, app_id)
    print(f"应用 API Key 前缀 {app_key[:12]}…")

    failures = smoke(
        app_key,
        [
            ("编译原理的先修要求是什么？我修过 CS101 和 CS201", "CS101,CS201"),
            ("毕业还差多少学分", "CS101,CS201"),
            ("转专业后学分怎么认定", "CS101"),
        ],
    )
    if failures:
        print(f"冒烟测试失败 {failures} 项")
        return 1
    print("SMOKE PASS：Chatflow 全链路（检索→条款整理→分流→核算→回答）通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""生成并导入一个"只用插件工具"的最小工作流，验证 .difypkg 在 Dify 里真能跑。

与 build_chatflow.py 的区别：那个应用调的是自研 HTTP 服务，这个应用只调本地
插件（课程核算内核随插件一起分发），因此它证明的是"插件本身可独立工作"。

用法：
    python dify-workflow/build_plugin_demo.py            # 只生成 DSL
    python dify-workflow/build_plugin_demo.py --import   # 导入 + 发布 + 冒烟
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = Path(__file__).parent / "plugin-audit.workflow.yml"
APP_NAME = "课径插件演示 · 学分缺口核算"

PROVIDER_ID = "coursepath/course_audit/course_audit"
PLUGIN_ID = "coursepath/course_audit"


def node(node_id: str, data: dict, x: int, y: int, width: int = 244, height: int = 90) -> dict:
    return {
        "data": data,
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


def edge(src: str, dst: str, src_type: str, dst_type: str) -> dict:
    return {
        "data": {"isInIteration": False, "sourceType": src_type, "targetType": dst_type},
        "id": f"{src}-{dst}",
        "source": src,
        "sourceHandle": "source",
        "target": dst,
        "targetHandle": "target",
    }


def build(plugin_unique_identifier: str) -> dict:
    start = node(
        "start",
        {
            "type": "start",
            "title": "开始",
            "desc": "输入已修课程，插件直接算出学分缺口",
            "variables": [
                {
                    "variable": "completed",
                    "label": "已修课程",
                    "description": "逗号分隔的课程代码，可带学分如 CS101:4",
                    "type": "text-input",
                    "required": False,
                    "max_length": 512,
                    "options": [],
                }
            ],
        },
        60,
        260,
    )
    tool = node(
        "tool",
        {
            "type": "tool",
            "title": "学分缺口核算（插件）",
            "desc": "调用本地 .difypkg 插件，内核随插件分发，不依赖外部服务",
            "provider_id": PROVIDER_ID,
            "provider_type": "plugin",
            "provider_name": PROVIDER_ID,
            "tool_name": "credit_audit",
            "tool_label": "Credit gap audit",
            "tool_configurations": {},
            "tool_parameters": {
                "completed": {"type": "mixed", "value": "{{#start.completed#}}"},
                "plan_json": {"type": "mixed", "value": ""},
            },
            "tool_node_version": "2",
            "plugin_unique_identifier": plugin_unique_identifier,
        },
        400,
        260,
    )
    answer = node(
        "answer",
        {"type": "answer", "title": "回答", "desc": "", "answer": "{{#tool.text#}}", "variables": []},
        760,
        260,
    )
    return {
        "app": {
            "description": "只用 .difypkg 插件完成学分缺口核算，用于验证插件可独立工作。",
            "icon": "📐",
            "icon_background": "#E4FBCC",
            "mode": "advanced-chat",
            "name": APP_NAME,
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": "app",
        "version": "0.3.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {"allowed_file_extensions": [], "allowed_file_types": ["image"], "allowed_file_upload_methods": ["local_file"], "enabled": False, "number_limits": 3, "retrieval_model": "local_file"},
                "opening_statement": "",
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {
                "edges": [
                    edge("start", "tool", "start", "tool"),
                    edge("tool", "answer", "tool", "answer"),
                ],
                "nodes": [start, tool, answer],
            },
        },
    }


def find_plugin_identifier(client) -> str:
    status, body = client.get("/console/api/workspaces/current/plugin/list")
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"读取插件列表失败 {status}: {body}")
    for item in body.get("plugins", []):
        if item.get("plugin_id") == PLUGIN_ID:
            return item["plugin_unique_identifier"]
    raise SystemExit(f"未找到已安装插件 {PLUGIN_ID}，先运行 deploy/install_plugin.py")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--identifier", default=None, help="插件 unique_identifier（默认从已装列表读取）")
    args = parser.parse_args()

    from build_chatflow import create_api_key, delete_existing_apps, import_app, publish_app
    from spike_dify import Dify

    identifier = args.identifier
    client = None
    if not identifier:
        client = Dify()
        client.login()
        identifier = find_plugin_identifier(client)
        print(f"插件 {identifier}")

    dsl = build(identifier)
    dsl_text = yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False)
    OUT_FILE.write_text(dsl_text, encoding="utf-8")
    print(f"已生成 {OUT_FILE.relative_to(ROOT)}")

    if not args.do_import:
        return 0

    client = client or Dify()
    if not getattr(client, "csrf", ""):
        client.login()
    delete_existing_apps(client, APP_NAME)
    app_id = import_app(client, dsl_text)
    publish_app(client, app_id)
    api_key = create_api_key(client, app_id)

    payload = {
        "inputs": {"completed": "CS101,CS201"},
        "query": "帮我看看还差多少学分",
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
        print(f"调用失败 {exc.code}: {exc.read().decode()[:800]}")
        return 1
    answer = (body.get("answer") or "").strip()
    print(f"\n插件回答：\n{answer[:800]}")
    ok = "还差" in answer and "学分" in answer
    print("\nPLUGIN TOOL PASS：插件在工作流中返回了可核对结论" if ok else "PLUGIN TOOL FAIL：回答不符合预期")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

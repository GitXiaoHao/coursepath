"""生成并导入一个"只用插件工具"的可直接聊天工作流。

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
APP_NAME = "课径插件演示 · 学业助手"

PROVIDER_ID = "coursepath/course_audit/course_audit"
PLUGIN_ID = "coursepath/course_audit"

# 模型只负责把自然语言归到工具类别，并提取用户明确声明“已修/已通过”的课程代码；
# 所有课程、学分、先修结论仍由插件代码计算。代码节点会校验 JSON，拒绝不在用户
# 输入中出现的代码，避免模型凭空补课或把目标课程误当已修课程。
INTENT_PARSE_CODE = '''
import json
import re

PREREQ_WORDS = ("先修", "前置", "能不能选", "能否选", "可以选")
PLAN_WORDS = ("修读规划", "选课建议", "推荐课程", "推荐我", "下一步修")

CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6}\\d{2,4})(?![A-Za-z0-9])")

def codes_in(text: str) -> list:
    return [item.upper() for item in CODE.findall(text or "")]

def target_codes_in(text: str) -> set:
    """排除紧邻“能选/先修”等问法的目标课程代码，避免模型误把它当已修。"""
    targets = set()
    raw = text or ""
    for match in CODE.finditer(raw):
        # 只看代码紧邻的短片段；不能把“VE201，VE205 能选吗”中的 VE201 误排除。
        before = raw[max(0, match.start() - 4):match.start()]
        after = raw[match.end():match.end() + 4]
        if any(word in before or word in after for word in ("能选", "选吗", "先修", "前置")):
            targets.add(match.group(1).upper())
    return targets

def main(model_text: str, query: str, form_completed: str) -> dict:
    raw = model_text or ""
    match = re.search(r"\\{.*?\\}", raw, re.S)
    data = {}
    if match:
        try:
            data = json.loads(match.group(0))
        except (TypeError, ValueError):
            data = {}
    intent = str(data.get("intent") or "").lower()
    if intent not in ("audit", "prereq", "study_plan"):
        text = query or ""
        if any(word in text for word in PREREQ_WORDS):
            intent = "prereq"
        elif any(word in text for word in PLAN_WORDS):
            intent = "study_plan"
        else:
            intent = "audit"
    # 表单是用户显式录入，优先保留。模型提取的课程必须同时：格式合法、原文出现、
    # 且用户这轮确实使用了完成状态表述；不从课程名称或常识推断代码。
    completed = codes_in(form_completed)
    completion_claimed = any(word in (query or "") for word in ("已修", "修过", "学过", "已通过", "通过了", "已完成", "修完"))
    model_courses = data.get("completed_courses") or []
    if isinstance(model_courses, str):
        model_courses = codes_in(model_courses)
    query_codes = set(codes_in(query))
    target_codes = target_codes_in(query)
    if completion_claimed and isinstance(model_courses, list):
        for item in model_courses:
            code = str(item).upper().strip()
            if re.fullmatch(r"[A-Z]{2,6}\\d{2,4}", code) and code in query_codes and code not in target_codes:
                completed.append(code)
    return {"intent": intent, "completed": ",".join(dict.fromkeys(completed))}
'''

CITE_CODE = '''
def main(docs: list) -> dict:
    if not docs:
        return {"text": "> 未检索到相关培养方案条款。"}
    lines = ["#### 可核对依据"]
    for i, doc in enumerate(docs[:3], 1):
        meta = doc.get("metadata") or {}
        nested = meta.get("doc_metadata") or {}
        page = nested.get("page") or meta.get("page")
        row = nested.get("table_row") or meta.get("table_row")
        loc = ("第 {} 页".format(page) if page else "")
        if row:
            loc = "{} 第 {} 行".format(loc, row) if loc else "第 {} 行".format(row)
        title = doc.get("title") or nested.get("title") or meta.get("doc_title") or "培养方案条款"
        content = " ".join((doc.get("content") or "").splitlines()).strip()
        lines.append("{}. {}（{}）：{}".format(i, title, loc or "位置未登记", content[:160]))
    return {"text": chr(10).join(lines)}
'''


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


def edge(src: str, dst: str, src_type: str, dst_type: str, *, handle: str = "source") -> dict:
    item = {
        "data": {"isInIteration": False, "sourceType": src_type, "targetType": dst_type},
        "id": f"{src}-{dst}",
        "source": src,
        "sourceHandle": handle,
        "target": dst,
        "targetHandle": "target",
    }
    return item


def response_llm(node_id: str, title: str, result_var: str, x: int, y: int) -> dict:
    """只润色确定性工具原文，禁止模型扩展任何学业事实。"""
    return node(
        node_id,
        {
            "type": "llm",
            "title": title,
            "desc": "只整理插件已给出的结论，不参与计算或判定。",
            "model": {
                "provider": "langgenius/tongyi/tongyi",
                "name": "qwen-plus-latest",
                "mode": "chat",
                "completion_params": {"temperature": 0.2},
            },
            "prompt_template": [
                {
                    "role": "system",
                    "text": "你是课径学业助手的表达层。只能依据【工具结果】回答，工具结果是唯一事实来源。不得添加、猜测、纠正或省略其中的课程、课程代码、学分、先修条件、可选结论或教务规则；工具结果无法判断时，直接说明缺少什么，不要自行判断。用自然、简洁的中文直接回答用户，通常 2–5 个短段落或列表；不要使用“关键依据”“下一步”“系统提示”等模板标题，不要提及模型、工作流或插件。修读建议必须保留“不能替代正式选课”的限制。",
                },
                {
                    "role": "user",
                    "text": "用户问题：{{#sys.query#}}\n\n【工具结果】\n{{#" + result_var + ".text#}}",
                },
            ],
            "prompt_config": {"jinja2_variables": []},
            "memory": None,
            "context": {"enabled": False, "variable_selector": []},
            "vision": {"enabled": False},
            "structured_output_enabled": False,
            "reasoning_format": "separated",
        },
        x,
        y,
        width=320,
        height=200,
    )


def build(plugin_unique_identifier: str, dataset_id: str | None = None) -> dict:
    start = node(
        "start",
        {
            "type": "start",
            "title": "开始",
            "desc": "直接提问学分、先修或修读规划；插件按问题类型自动分流",
            "variables": [
                {
                    "variable": "completed",
                    "label": "已修课程",
                    "description": "逗号分隔的课程代码，可带学分如 CS101:4",
                    "type": "text-input",
                    "required": False,
                    "max_length": 512,
                    "options": [],
                },
                {
                    "variable": "plan_json",
                    "label": "培养方案 JSON（可选）",
                    "description": "留空使用内置演示方案；真实部署时由学校提供经核验的方案数据。",
                    "type": "paragraph",
                    "required": False,
                    "max_length": 30000,
                    "options": [],
                },
            ],
        },
        60,
        260,
    )
    llm_intent = node(
        "llm_intent",
        {
            "type": "llm",
            "title": "千问意图识别",
            "desc": "仅识别应调用的工具；不计算、不补充任何课程事实。",
            "model": {
                "provider": "langgenius/tongyi/tongyi",
                "name": "qwen-plus-latest",
                "mode": "chat",
                "completion_params": {"temperature": 0},
            },
            "prompt_template": [
                {
                    "role": "system",
                    "text": "你是严格的工作流路由器，只输出 JSON，不要 Markdown 或解释。intent 只能是 audit、prereq、study_plan。询问毕业还差多少学分、已修课程后剩多少学分、模块学分缺口为 audit；询问课程先修条件、是否能选课为 prereq；询问下一步修什么、推荐课程、修读或选课规划为 study_plan。无法确定时输出 audit。completed_courses 只能列出用户明确表示已修、修过、已通过或修完的课程代码；不要把正在询问能否选的目标课程列入，不要从课程名称猜课程代码，不要补全。格式：{\\\"intent\\\":\\\"audit\\\",\\\"completed_courses\\\":[\\\"CS101\\\"]}",
                },
                {"role": "user", "text": "用户问题：{{#sys.query#}}"},
            ],
            "prompt_config": {"jinja2_variables": []},
            "memory": None,
            "context": {"enabled": False, "variable_selector": []},
            "vision": {"enabled": False},
            "structured_output_enabled": False,
            "reasoning_format": "separated",
        },
        380,
        220,
        width=320,
        height=220,
    )
    intent_parse = node(
        "intent_parse",
        {
            "type": "code",
            "title": "校验意图",
            "desc": "校验千问输出；仅合并用户明确声明的已修课程，异常时保守回退。",
            "code_language": "python3",
            "code": INTENT_PARSE_CODE,
            "variables": [
                {"variable": "model_text", "value_selector": ["llm_intent", "text"]},
                {"variable": "query", "value_selector": ["sys", "query"]},
                {"variable": "form_completed", "value_selector": ["start", "completed"]},
            ],
            "outputs": {
                "intent": {"type": "string", "children": None},
                "completed": {"type": "string", "children": None},
            },
        },
        760,
        250,
        width=260,
        height=150,
    )
    kb = None
    cite = None
    if dataset_id:
        kb = node(
            "kb",
            {
                "type": "knowledge-retrieval", "title": "培养方案知识库检索",
                "query_variable_selector": ["sys", "query"], "dataset_ids": [dataset_id],
                "retrieval_mode": "multiple",
                "multiple_retrieval_config": {"top_k": 3, "score_threshold": None, "reranking_enable": False, "reranking_mode": "reranking_model", "reranking_model": None, "weights": None},
                "metadata_filtering_mode": "disabled", "vision": {"enabled": False},
            }, 380, 260, width=300, height=120,
        )
        cite = node(
            "cite",
            {"type": "code", "title": "整理可核对引用", "code_language": "python3", "code": CITE_CODE,
             "variables": [{"variable": "docs", "value_selector": ["kb", "result"]}],
             "outputs": {"text": {"type": "string", "children": None}}},
            720, 260, width=280, height=150,
        )
    branch = node(
        "branch",
        {
            "type": "if-else",
            "title": "按意图分流（千问 + 校验）",
            "logical_operator": "or",
            "cases": [
                {
                    "case_id": "prereq",
                    "logical_operator": "or",
                    "conditions": [{"variable_selector": ["intent_parse", "intent"], "comparison_operator": "contains", "value": "prereq"}],
                },
                {
                    "case_id": "study_plan",
                    "logical_operator": "or",
                    "conditions": [{"variable_selector": ["intent_parse", "intent"], "comparison_operator": "contains", "value": "study_plan"}],
                },
            ],
        },
        1090,
        260,
        width=280,
        height=160,
    )

    audit = node(
        "audit",
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
                "completed": {"type": "mixed", "value": "{{#intent_parse.completed#}}"},
                "plan_json": {"type": "mixed", "value": "{{#start.plan_json#}}"},
            },
            "tool_node_version": "2",
            "plugin_unique_identifier": plugin_unique_identifier,
        },
        1440,
        480,
    )
    prereq = node(
        "prereq",
        {
            "type": "tool",
            "title": "先修判定（插件）",
            "desc": "从问题中识别目标课程，并用已修课程做确定性判定",
            "provider_id": PROVIDER_ID,
            "provider_type": "plugin",
            "provider_name": PROVIDER_ID,
            "tool_name": "prereq_check",
            "tool_label": "Prerequisite check",
            "tool_configurations": {},
            "tool_parameters": {
                "course": {"type": "mixed", "value": "{{#sys.query#}}"},
                "completed": {"type": "mixed", "value": "{{#intent_parse.completed#}}"},
                "plan_json": {"type": "mixed", "value": "{{#start.plan_json#}}"},
            },
            "tool_node_version": "2",
            "plugin_unique_identifier": plugin_unique_identifier,
        },
        1440,
        60,
    )
    planner = node(
        "planner",
        {
            "type": "tool",
            "title": "修读规划（插件）",
            "desc": "只依据培养方案先修规则与模块缺口生成候选课，不替代教务选课",
            "provider_id": PROVIDER_ID,
            "provider_type": "plugin",
            "provider_name": PROVIDER_ID,
            "tool_name": "study_plan",
            "tool_label": "Study plan suggestions",
            "tool_configurations": {},
            "tool_parameters": {
                "completed": {"type": "mixed", "value": "{{#intent_parse.completed#}}"},
                "limit": {"type": "constant", "value": 6},
                "plan_json": {"type": "mixed", "value": "{{#start.plan_json#}}"},
            },
            "tool_node_version": "2",
            "plugin_unique_identifier": plugin_unique_identifier,
        },
        1440,
        270,
    )
    answer_prereq = node(
        "answer_prereq",
        {"type": "answer", "title": "先修结果", "desc": "", "answer": "{{#llm_prereq.text#}}" + ("\n\n---\n{{#cite.text#}}" if dataset_id else ""), "variables": []},
        2160,
        60,
    )
    # 修读规划含模块缺口等计算结论，直接展示插件的确定性原文，避免表达层
    # 将“总缺口”误写成某一模块缺口；泛化检索不到时也不追加无关的“未检索到”。
    answer_plan = node(
        "answer_plan",
        {"type": "answer", "title": "规划结果", "desc": "", "answer": "{{#planner.text#}}", "variables": []},
        2160,
        270,
    )
    answer_audit = node(
        "answer_audit",
        {"type": "answer", "title": "核算结果", "desc": "", "answer": "{{#llm_audit.text#}}" + ("\n\n---\n{{#cite.text#}}" if dataset_id else ""), "variables": []},
        2160,
        480,
    )
    return {
        "app": {
            "description": "安装课径学业助手插件后即可导入的聊天工作流：自动分流学分、先修与修读规划问题。",
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
                "edges": ([
                    edge("start", "kb", "start", "knowledge-retrieval"),
                    edge("kb", "cite", "knowledge-retrieval", "code"),
                    edge("cite", "llm_intent", "code", "llm"),
                ] if dataset_id else [edge("start", "llm_intent", "start", "llm")]) + [
                    edge("llm_intent", "intent_parse", "llm", "code"),
                    edge("intent_parse", "branch", "code", "if-else"),
                    edge("branch", "prereq", "if-else", "tool", handle="prereq"),
                    edge("branch", "planner", "if-else", "tool", handle="study_plan"),
                    edge("branch", "audit", "if-else", "tool", handle="false"),
                    edge("prereq", "llm_prereq", "tool", "llm"),
                    edge("planner", "answer_plan", "tool", "answer"),
                    edge("audit", "llm_audit", "tool", "llm"),
                    edge("llm_prereq", "answer_prereq", "llm", "answer"),
                    edge("llm_audit", "answer_audit", "llm", "answer"),
                ],
                "nodes": ([start, kb, cite] if dataset_id else [start]) + [
                    llm_intent, intent_parse, branch, prereq, planner, audit,
                    response_llm("llm_prereq", "千问润色（先修）", "prereq", 1780, 60),
                    response_llm("llm_audit", "千问润色（核算）", "audit", 1780, 480),
                    answer_prereq, answer_plan, answer_audit,
                ],
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
    parser.add_argument("--with-knowledge", action="store_true", help="接入已登记的 campus-kb 外部知识库并在回答附依据")
    parser.add_argument("--dataset-id", default=None, help="外部知识库 dataset id（与 --with-knowledge 一起使用）")
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

    dataset_id = args.dataset_id
    if args.with_knowledge:
        from spike_dify import ensure_external_dataset

        if not dataset_id:
            client = client or Dify()
            if not getattr(client, "csrf", ""):
                client.login()
            dataset_id = ensure_external_dataset(client)
        print(f"知识库 {dataset_id}")
    elif dataset_id:
        parser.error("--dataset-id 需要同时使用 --with-knowledge")

    dsl = build(identifier, dataset_id=dataset_id)
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

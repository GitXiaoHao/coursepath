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

try:
    import yaml
except ModuleNotFoundError:  # 便于仅生成 DSL 的极简 Windows 环境；JSON 也是合法 YAML。
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))

OUT_FILE = Path(__file__).parent / "course-audit.chatflow.yml"

#: 先修类问题的关键词（if-else 的 or 条件），命中任一即走 /prereq 分支。
#: "能选" 一个词覆盖 能不能选 / 能否选 / 能选吗 / 还能选修吗 等常见问法；
#: 早期只写了「能不能选」「能否选」，学生问「XX 能选吗」会掉进学分核算分支。
PREREQ_KEYWORDS = ["先修", "前置", "能选", "可以选", "可不可以选", "选课条件"]
#: 条款类问题的关键词（转专业、认定、免修等），走"检索条款"分支
CLAUSE_KEYWORDS = ["转专业", "学分认定", "免修", "重修", "替代", "抵", "条例", "规定", "政策"]
#: 核算类问题的关键词。v2 把它从默认分支变成显式分支，避免普通条款问题
#: 也被误送进学分核算。
AUDIT_KEYWORDS = ["毕业", "还差", "缺多少", "学分缺口", "已修学分", "总学分", "完成学分"]

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
        source_url = doc_meta.get("source_url") or meta.get("source_url") or ""
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
        # 仅渲染可实际访问的链接；示例数据的 synthetic:// 地址会在前端显示为
        # [blocked]，反而让用户误以为资料丢失。页码/行号仍保留作可核对依据。
        link = " [来源链接]({})".format(source_url) if source_url.startswith(("http://", "https://")) else ""
        lines.append("{}. {}（{}，匹配度 {:.2f}）：{}{}".format(i, name, loc or "位置未登记", score, content, link))
    return {"text": "\\n".join(lines)}
'''

# 千问意图识别的输出不是事实来源，只负责把自然语言标准化为一个可路由的标签和
# 课程代码列表。代码节点会校验 JSON，并保留关键词回退，避免模型偶发格式错误让
# 工作流中断。
INTENT_PARSE_CODE = '''
import json
import re

AUDIT_WORDS = ("毕业", "还差", "缺多少", "学分缺口", "已修学分", "总学分", "完成学分", "修完", "已修", "学过", "通过")
PREREQ_WORDS = ("先修", "前置", "能不能选", "可以选", "能否选")

def main(model_text: str, query: str, form_completed: str) -> dict:
    raw = model_text or ""
    # 支持模型偶尔包裹在 Markdown code fence 里的 JSON。
    match = re.search(r"\\{.*?\\}", raw, re.S)
    data = {}
    if match:
        try:
            data = json.loads(match.group(0))
        except (TypeError, ValueError):
            data = {}

    intent = str(data.get("intent") or "").lower()
    text = query or ""
    if intent not in ("audit", "prereq", "clause"):
        if any(word in text for word in PREREQ_WORDS):
            intent = "prereq"
        elif any(word in text for word in AUDIT_WORDS):
            intent = "audit"
        else:
            intent = "clause"

    # 只接受规范课程代码；表单输入优先保留，模型抽取的代码补充到其后。
    found = re.findall(r"\\b[A-Za-z]{2,6}\\d{2,4}\\b", form_completed or "")
    model_courses = data.get("completed_courses") or []
    if isinstance(model_courses, str):
        model_courses = re.findall(r"\\b[A-Za-z]{2,6}\\d{2,4}\\b", model_courses)
    if isinstance(model_courses, list):
        for course in model_courses:
            value = str(course).upper().strip()
            if re.fullmatch(r"[A-Z]{2,6}\\d{2,4}", value):
                found.append(value)
    completed = ",".join(dict.fromkeys(code.upper() for code in found))
    return {"intent": intent, "completed": completed}
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


def build(api_key: str, dataset_id: str, *, v2: bool = False, smart: bool = False, intent: bool = False) -> dict:
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
    audit_conditions = [
        {
            "variable_selector": ["sys", "query"],
            "comparison_operator": "contains",
            "value": keyword,
        }
        for keyword in AUDIT_KEYWORDS
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
                        "label": "已修课程代码（可选，逗号分隔，如 CS101,EE101,VE201）",
                        "type": "text-input",
                        "required": False,
                        "max_length": 512,
                        "options": [],
                    },
                    {
                        # 可选：填了才把「已修课程」存进档案；不填就是一次性核算。
                        # 3–128 位字母/数字/_ . : -，服务端用同一正则校验。
                        "variable": "profile_id",
                        "label": "档案标识（可选）",
                        "description": "想跨会话记住已修课程时，自定一个固定标识（如学号或匿名昵称）；不填则只做一次性核算。",
                        "type": "text-input",
                        "required": False,
                        "max_length": 128,
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
                    *(
                        [
                            {
                                "case_id": "audit",
                                "logical_operator": "or",
                                "conditions": [dict(c) for c in audit_conditions],
                            }
                        ]
                        if v2
                        else [
                            {
                                "case_id": "clause",
                                "logical_operator": "or",
                                "conditions": [dict(c) for c in clause_conditions],
                            }
                        ]
                    ),
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
            {"course_name": "{{#sys.query#}}", "completed_text": "{{#start.completed#}}", "profile_id": "{{#start.profile_id#}}", "format": "brief"},
            api_key,
            1420,
            60,
        ),
        http_node(
            "audit",
            "学分核算 /audit",
            "/audit",
            {"completed_text": "{{#start.completed#}}", "profile_id": "{{#start.profile_id#}}", "format": "text"},
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
                    "### 我查到的相关信息\n"
                    "以下内容来自当前培养方案知识库；我没有补充未在原文中出现的规则。\n\n"
                    "{{#cite.text#}}\n\n"
                    "> 如需判断自己是否符合条件，请补充课程代码、已修课程和培养方案版本；"
                    "涉及转专业、免修或学分认定，请以教务最终审核为准。"
                    if v2
                    else "### 条款速览\n"
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

    # v3: 保留核算服务作为唯一事实来源；千问只负责把已得到的结果组织成自然语言。
    # 这让表达更像咨询助手，同时不会由模型重算学分或改写先修结论。
    if smart:
        def llm_node(node_id: str, title: str, x: int, y: int, system: str, user: str) -> dict:
            return node(
                node_id,
                {
                    "type": "llm",
                    "title": title,
                    "desc": "仅整理系统结果与检索条款，不改变事实。",
                    "model": {
                        "provider": "langgenius/tongyi/tongyi",
                        "name": "qwen-plus-latest",
                        "mode": "chat",
                        "completion_params": {"temperature": 0.2},
                    },
                    "prompt_template": [
                        {"role": "system", "text": system},
                        {"role": "user", "text": user},
                    ],
                    "prompt_config": {"jinja2_variables": []},
                    "memory": None,
                    "context": {"enabled": False, "variable_selector": []},
                    "vision": {"enabled": False},
                    "structured_output_enabled": False,
                    "reasoning_format": "separated",
                },
                x, y, width=320, height=260,
            )

        audit_style = (
            "你是课径的学分结果表达层。只依据【系统学分核算】中的事实；不得改变数字、课程、模块"
            "或规则，不得补充任何未出现的政策或建议。用自然中文回答：首句直接说总剩余学分；随后"
            "用一行或短列表给出系统已有的模块缺口。不要使用“关键依据”“下一步”“结论”等模板标题，"
            "不要复述计算过程、规则编号、模型、工作流或技术实现。除非系统无法核算，否则不要求用户"
            "再次提供已经保存的课程。"
        )
        prereq_style = (
            "你是课径的先修结果表达层。只依据【系统先修判定】中的事实；系统结果已合并本轮输入和"
            "已保存的课程档案。回答最多两句：首句只能是“可以选。”或“暂不能选。”；若不能选，"
            "第二句只列出系统明确给出的直接缺失先修课。若可以选，不写第二句。不要解释已满足的"
            "课程、学分、开课学期、修读建议或其他延伸信息；不要使用“关键依据”“下一步”等模板"
            "标题，也不要提及档案键、模型、工作流或技术实现。"
        )
        clause_style = (
            "你是课径的培养方案问答表达层。只根据检索到的条款回答；资料没有明确写出的内容，就直接"
            "说“当前资料未说明”。先给简短答案，必要时补一两句说明。不要猜测、不要编造，不使用"
            "“关键依据”“下一步”“结论”等模板标题。"
        )
        nodes.extend([
            llm_node("llm_prereq", "千问润色（先修）", 1760, 60, prereq_style,
                     "用户问题：{{#sys.query#}}\n\n【系统先修判定】\n{{#prereq.body#}}\n\n【检索依据】\n{{#cite.text#}}"),
            llm_node("llm_audit", "千问润色（学分）", 1760, 260, audit_style,
                     "用户问题：{{#sys.query#}}\n\n【系统学分核算】\n{{#audit.body#}}\n\n【检索依据】\n{{#cite.text#}}"),
            llm_node("llm_clause", "千问问答（条款）", 1420, 500, clause_style,
                     "用户问题：{{#sys.query#}}\n\n【检索到的培养方案条款】\n{{#cite.text#}}"),
        ])
        by_id = {item["id"]: item for item in nodes}
        by_id["answer_prereq"]["data"]["answer"] = "{{#prereq.body#}}\n\n---\n{{#cite.text#}}"
        by_id["answer_audit"]["data"]["answer"] = "{{#llm_audit.text#}}\n\n---\n{{#cite.text#}}"
        by_id["answer_clause"]["data"]["answer"] = "{{#llm_clause.text#}}\n\n---\n{{#cite.text#}}"
        by_id["answer_prereq"]["position"] = {"x": 2140, "y": 60}
        by_id["answer_audit"]["position"] = {"x": 2140, "y": 260}
        by_id["answer_clause"]["position"] = {"x": 1800, "y": 500}
        by_id["answer_prereq"]["positionAbsolute"] = dict(by_id["answer_prereq"]["position"])
        by_id["answer_audit"]["positionAbsolute"] = dict(by_id["answer_audit"]["position"])
        by_id["answer_clause"]["positionAbsolute"] = dict(by_id["answer_clause"]["position"])

    # v4：先由千问识别意图、抽取自然语言中出现的已修课程代码，再由代码节点做
    # JSON 校验和保守回退。后续的 /audit、/prereq 仍是唯一的事实与计算来源。
    if intent:
        if not smart:
            raise ValueError("intent 路由依赖 smart 模式中的千问节点")
        nodes.extend([
            node(
                "llm_intent",
                {
                    "type": "llm",
                    "title": "千问意图识别与课程提取",
                    "desc": "只分类和提取课程代码，不计算、不回答事实。",
                    "model": {
                        "provider": "langgenius/tongyi/tongyi",
                        "name": "qwen-plus-latest",
                        "mode": "chat",
                        "completion_params": {"temperature": 0},
                    },
                    "prompt_template": [
                        {"role": "system", "text": "你是严格的工作流路由器。只输出 JSON，不要 Markdown 或解释。intent 只能是 audit、prereq、clause：询问毕业剩余/已修课程/课程修完后剩余学分为 audit；询问课程先修或能否选课为 prereq；其余培养方案条款为 clause。completed_courses 只能包含用户明确说已修完、已通过、已修的课程代码；不要猜测课程名称对应的代码。输出格式：{\\\"intent\\\":\\\"audit\\\",\\\"completed_courses\\\":[\\\"GE101\\\"]}"},
                        {"role": "user", "text": "用户问题：{{#sys.query#}}\\n表单已修课程：{{#start.completed#}}"},
                    ],
                    "prompt_config": {"jinja2_variables": []},
                    "memory": None,
                    "context": {"enabled": False, "variable_selector": []},
                    "vision": {"enabled": False},
                    "structured_output_enabled": False,
                    "reasoning_format": "separated",
                },
                1080, 40, width=340, height=240,
            ),
            node(
                "intent_parse",
                {
                    "type": "code",
                    "title": "校验意图与已修课程",
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
                1460, 40, width=320, height=170,
            ),
        ])
        by_id = {item["id"]: item for item in nodes}
        by_id["branch"]["data"]["title"] = "按意图分流（千问 + 校验）"
        by_id["branch"]["data"]["cases"] = [
            {"case_id": "profile_manage", "logical_operator": "or", "conditions": [
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "删除"},
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "清空"},
            ]},
            {"case_id": "study_plan", "logical_operator": "or", "conditions": [
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "修读规划"},
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "选课建议"},
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "推荐课程"},
            ]},
            {"case_id": "profile", "logical_operator": "or", "conditions": [
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "我的档案"},
                {"variable_selector": ["sys", "query"], "comparison_operator": "contains", "value": "查看档案"},
            ]},
            {"case_id": "true", "logical_operator": "and", "conditions": [{"variable_selector": ["intent_parse", "intent"], "comparison_operator": "contains", "value": "prereq"}]},
            {"case_id": "audit", "logical_operator": "and", "conditions": [{"variable_selector": ["intent_parse", "intent"], "comparison_operator": "contains", "value": "audit"}]},
        ]
        nodes.extend([
            http_node(
                "profile",
                "查看我的课程档案",
                "/profile-summary",
                {"profile_id": "{{#start.profile_id#}}", "format": "text"},
                api_key,
                1420,
                500,
            ),
            node(
                "answer_profile",
                {"type": "answer", "title": "回答（我的档案）", "answer": "{{#profile.body#}}"},
                1760,
                500,
                width=300,
                height=140,
            ),
            http_node(
                "study_plan",
                "生成修读规划",
                "/study-plan",
                {"profile_id": "{{#start.profile_id#}}", "format": "text"},
                api_key,
                1420,
                700,
            ),
            node(
                "answer_study_plan",
                {"type": "answer", "title": "回答（修读规划）", "answer": "{{#study_plan.body#}}"},
                1780,
                700,
                width=300,
                height=140,
            ),
            http_node(
                "profile_manage",
                "档案安全管理",
                "/profile-command",
                {"profile_id": "{{#start.profile_id#}}", "query": "{{#sys.query#}}", "format": "text"},
                api_key,
                1420,
                900,
            ),
            node(
                "answer_profile_manage",
                {"type": "answer", "title": "回答（档案安全管理）", "answer": "{{#profile_manage.body#}}"},
                1780,
                900,
                width=300,
                height=140,
            ),
        ])
        by_id["audit"]["data"]["body"]["data"][0]["value"] = json.dumps({"completed_text": "{{#intent_parse.completed#}}", "profile_id": "{{#start.profile_id#}}", "format": "text"}, ensure_ascii=False)
        by_id["prereq"]["data"]["body"]["data"][0]["value"] = json.dumps({"course_name": "{{#sys.query#}}", "completed_text": "{{#intent_parse.completed#}}", "profile_id": "{{#start.profile_id#}}", "format": "brief"}, ensure_ascii=False)
        # 核算服务会合并本轮输入与该用户的已修课程档案，模型必须以系统结果为准。
        by_id["llm_prereq"]["data"]["desc"] = "系统判定已合并本轮输入与已修课程档案。"
        by_id["llm_prereq"]["data"]["prompt_template"] = [
            {
                "role": "system",
                "text": "你是课径的先修课咨询助手。只依据输入中的系统先修判定和检索依据回答。系统先修判定已合并本轮输入与该用户保存的已修课程档案；" 
                "无论【已识别已修课程】是否为空，都必须以系统的满足/不满足结论及缺失课程为准。不要因用户本轮未重复课程代码而说无法判断，" 
                "也不要提及档案键、工作流或技术实现。不要讲上游间接先修、修读顺序或泛泛建议，除非系统结果明确要求。" 
                "不编造课程、模块、政策或学分。输出 2 至 4 个短段落，不使用“关键依据/下一步”这种固定报告标题。",
            },
            {
                "role": "user",
                "text": "用户问题：{{#sys.query#}}\\n【已识别已修课程】{{#intent_parse.completed#}}\\n\\n【系统先修判定】\\n{{#prereq.body#}}\\n\\n【检索依据】\\n{{#cite.text#}}",
            },
        ]

    edges = [
        edge("start", "kb", "start", "knowledge-retrieval"),
        edge("kb", "cite", "knowledge-retrieval", "code"),
        edge("cite", "branch", "code", "if-else"),
        edge("branch", "prereq", "if-else", "http-request", handle="true"),
        edge("branch", "audit", "if-else", "http-request", handle="audit" if v2 else "false"),
        edge("branch", "answer_clause", "if-else", "answer", handle="false" if v2 else "clause"),
        edge("prereq", "answer_prereq", "http-request", "answer"),
        edge("audit", "answer_audit", "http-request", "answer"),
    ]
    if smart:
        edges = [item for item in edges if item["target"] not in {"answer_prereq", "answer_audit", "answer_clause"}]
        edges.extend([
            edge("prereq", "llm_prereq", "http-request", "llm"),
            edge("llm_prereq", "answer_prereq", "llm", "answer"),
            edge("audit", "llm_audit", "http-request", "llm"),
            edge("llm_audit", "answer_audit", "llm", "answer"),
            edge("branch", "llm_clause", "if-else", "llm", handle="false"),
            edge("llm_clause", "answer_clause", "llm", "answer"),
        ])
    if intent:
        # 路由从 intent_parse 进入，而不是直接读取检索条款。
        edges = [item for item in edges if not (item["source"] == "cite" and item["target"] == "branch")]
        edges.extend([
            edge("cite", "llm_intent", "code", "llm"),
            edge("llm_intent", "intent_parse", "llm", "code"),
            edge("intent_parse", "branch", "code", "if-else"),
            edge("branch", "profile", "if-else", "http-request", handle="profile"),
            edge("profile", "answer_profile", "http-request", "answer"),
            edge("branch", "study_plan", "if-else", "http-request", handle="study_plan"),
            edge("study_plan", "answer_study_plan", "http-request", "answer"),
            edge("branch", "profile_manage", "if-else", "http-request", handle="profile_manage"),
            edge("profile_manage", "answer_profile_manage", "http-request", "answer"),
        ])

    return {
        "app": {
            "description": "「课径」：把培养方案条款变成可核算的问答，每题都给引用与计算过程。",
            "icon": "📘",
            "icon_background": "#E0F2FE",
            "mode": "advanced-chat",
            "name": "课径 · 可核算的培养方案问答 v8（精简回复版）" if intent else ("课径 · 可核算的培养方案问答 v3（智能版）" if smart else ("课径 · 可核算的培养方案问答 v2" if v2 else "课径 · 可核算的培养方案问答")),
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
                    "1. 我修完这些课还差多少学分？（先在下方填已修课程代码，如 GE101,ME101）\n"
                    "2. 汽车电子控制的先修要求是什么？我能选吗？\n"
                    "每条回答都会给出依据条款与计算步骤。"
                ),
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                     "毕业还差多少学分？",
                     "汽车电子控制的先修要求是什么？",
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
    parser.add_argument("--v2", action="store_true", help="生成不误触发核算的单路由 v2 DSL")
    parser.add_argument("--smart", action="store_true", help="生成带千问表达层的 v3 DSL（同时启用 v2 路由）")
    parser.add_argument("--intent", action="store_true", help="生成带千问意图识别与课程代码提取的 v4 DSL（依赖 --smart）")
    parser.add_argument("--keep-existing", action="store_true", help="导入时保留已有应用，不删除同名旧版")
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

    if args.intent and not args.smart:
        parser.error("--intent 需要同时使用 --smart")
    dsl = build(api_key, dataset_id, v2=(args.v2 or args.smart), smart=args.smart, intent=args.intent)
    dsl_text = yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False) if yaml else json.dumps(dsl, ensure_ascii=False, indent=2)
    # 落盘的 DSL 把密钥替换成占位符：这份文件要进仓库/进提交包，
    # 明文密钥会被判"泄露凭据"。导入时仍用内存里的真实值。
    out_file = OUT_FILE.with_name("course-audit-v4-intent.chatflow.yml") if args.intent else (OUT_FILE.with_name("course-audit-v3-smart.chatflow.yml") if args.smart else (OUT_FILE.with_name("course-audit-v2.chatflow.yml") if args.v2 else OUT_FILE))
    out_file.write_text(dsl_text.replace(api_key, "${CAMPUS_KB_API_KEY}"), encoding="utf-8")
    print(f"已生成 {out_file.relative_to(ROOT)}（密钥已替换为 ${{CAMPUS_KB_API_KEY}}）")

    if not args.do_import:
        return 0

    client = client or Dify()
    if not getattr(client, "csrf", ""):
        client.login()
    if not args.keep_existing:
        delete_existing_apps(client)
    app_id = import_app(client, dsl_text)
    publish_app(client, app_id)
    app_key = create_api_key(client, app_id)
    print(f"应用 API Key 前缀 {app_key[:12]}…")

    failures = smoke(
        app_key,
        [
            # 测试 1：复杂的联合先修（VE205 需要 EE101、CS101、VE201 全部修完）
            ("汽车电子控制的先修要求是什么？我修过 CS101、EE101 和 VE201", "CS101,EE101,VE201"),
            # 测试 2：学分核算（GE101 是 4 学分，ME101 是 3 学分，CS101 是 3 学分，总共 10 学分）
            ("毕业还差多少学分", "GE101,ME101,CS101"),
            # 测试 3：非核算条款检索
            ("转专业后学分怎么认定", ""),
        ],
    )
    if failures:
        print(f"冒烟测试失败 {failures} 项")
        return 1
    print("SMOKE PASS：Chatflow 全链路（检索→条款整理→分流→核算→回答）通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

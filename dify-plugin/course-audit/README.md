# Course path audit (课径学业助手)

A Dify **tool plugin** that turns a university study plan into an installable academic
assistant: it computes module credit gaps, checks prerequisites, and suggests courses
whose registered prerequisites are currently satisfied.

The point of the plugin is that **all arithmetic is deterministic Python**, not a
language model. Every answer carries a rule number (`R1`–`R5`) so a student or an
advisor can check the conclusion line by line.

## Tools

| Tool | Purpose | Key inputs |
| --- | --- | --- |
| `credit_audit` | Remaining credits per module + total | `completed` (required), `plan_json` (optional) |
| `prereq_check` | Can this course be taken now; missing direct/upstream prerequisites | `course` (required), `completed`, `plan_json` |
| `study_plan` | Courses currently worth considering, ranked by module gap | `completed`, `limit`, `plan_json` |

`completed` accepts `EE101,CS101`, `EE101:4、CS101`, `EE101(4)` and similar forms
(comma, semicolon, Chinese comma, ideographic comma).

`plan_json` is optional. When omitted the plugin uses a bundled synthetic demo plan
(no real institution data). Pass your own plan to audit a real one.

## Study plan JSON

```json
{
  "plan_id": "plan-demo-2026",
  "major": "Demo Major",
  "grade": "2026",
  "version": "1.0.0",
  "total_credits": 160,
  "modules": [{ "module": "General Education", "required": 48 }],
  "courses": [
    {
      "code": "CS305",
      "name": "Compilers",
      "credits": 3.5,
      "module": "Core",
      "terms": ["5"],
      "prerequisites": { "kind": "and", "children": [
        { "kind": "course", "course_code": "CS101" },
        { "module": "Core", "kind": "credits", "min_credits": 40 }
      ]}
    }
  ]
}
```

Prerequisite expressions support three node kinds:

- `{"kind": "course", "course_code": "CS101"}` — a specific course must be passed
- `{"kind": "and"|"or", "children": [...]}` — boolean combination
- `{"kind": "credits", "module": "Core", "min_credits": 40}` — enough credits in a module

## Accounting rules

| Rule | Meaning |
| --- | --- |
| `R1` | Only courses with `passed: true` count toward credits |
| `R2` | Courses that are not registered in the plan are reported as *unrecognized*, never counted |
| `R3` | Credits always come from the plan, not from the student's claim (mismatch raises a warning) |
| `R4` | Surplus credits in one module never offset a deficit in another |
| `R5` | Remaining credits = sum of per-module gaps (not `total - earned`) |

## What is included, and what is optional

The plugin performs all calculations in-process and makes **no network calls**. It has
no API key, endpoint, or OAuth configuration step, so a Dify user can install it and
use the three tools immediately.

The bundled plan is explicitly synthetic and is synchronized from
`campus-kb/data/samples/plan.json`, the same source used by the local external
knowledge service. Every module and course carries its source page/table-row reference.
A school, department, or competition team can replace that source with an authorized,
verified `plan_json`, run `python dify-plugin/sync_plan.py`, and repack the plugin.
That keeps every installed copy on the same auditable rules without pointing at our
local `campus-kb` service.

Persistent personal records are deliberately **not** stored inside this portable
plugin. A record that survives conversations needs an authenticated identity and a
database controlled by the deploying school. The existing `campus-kb` service is the
optional server-side adapter for that advanced deployment; it is not a prerequisite for
the downloadable plugin.

## Install

1. Install the `.difypkg` file from this repository release in Dify
   (**Plugins → Install from local package**).
2. For a ready-to-chat application, run the workflow installer from the repository
   root after setting the local Dify administrator credentials:

   ```powershell
   $env:DIFY_ADMIN_PASSWORD = '<local Dify admin password>'
   python .\dify-workflow\build_plugin_demo.py --import
   ```

   It looks up the **installed** plugin identifier, generates the application DSL,
   imports it, and publishes `课径插件演示 · 学业助手`. This extra generation step is
   necessary because each Dify installation assigns a different plugin identifier.
   The template uses a configured Tongyi/Qwen `qwen-plus-latest` model only for intent
   recognition and response phrasing; change those nodes to another installed chat
   model if your Dify instance uses a different provider. Credit, prerequisite, and
   planning results still come exclusively from the deterministic plugin tools. The
   response nodes are explicitly instructed not to add, alter, or omit academic facts.

   For the full local deployment, use the existing `campus-kb` external knowledge
   service and add `--with-knowledge`:

   ```powershell
   python .\dify-workflow\build_plugin_demo.py --import --with-knowledge
   ```

   The installer reuses or registers `课径 · 培养方案知识库`, then adds retrieved
   page/table-row evidence below each answer. Without this flag the same workflow
   remains a fully portable, offline plugin application; it simply has no RAG citations.
3. In the new app, fill in **Completed courses** (for example `EE101,CS101`) and ask:
   “还差多少学分？”, “汽车电子控制能选吗？”, or “推荐我下一步修什么？”.

   The template also extracts course codes when the user explicitly writes a completion
   claim such as “我已修 EE101、CS101”. It merges those codes with the form field, but
   does not infer codes from course names or treat the target of “汽车电子控制能选吗？” as a
   completed course.

If you are building your own workflow, pick **课径学业助手** in a Tool node and choose
`credit_audit`, `prereq_check`, or `study_plan`. Each tool emits a Markdown summary and
a structured JSON message.

## Requirements

- Dify `1.0.0` or newer (tested on `1.17.1`)
- No outbound network access required
- The current package contains synthetic demo data only; verify and supply the real
  institution's plan before making real academic decisions
- Python runtime is provided by Dify's plugin runner (no manual dependency install)

## Source

Full project (external knowledge-base service, Chatflow DSL, evaluation harness):
see https://github.com/GitXiaoHao/coursepath.

## License

Apache-2.0.

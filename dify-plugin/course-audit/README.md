# Course path audit (课径核算)

A Dify **tool plugin** that audits a university study plan: how many credits are still
missing per module, and whether a course's prerequisites are satisfied.

The point of the plugin is that **all arithmetic is deterministic Python**, not a
language model. Every answer carries a rule number (`R1`–`R5`) so a student or an
advisor can check the conclusion line by line.

## Tools

| Tool | Purpose | Key inputs |
| --- | --- | --- |
| `credit_audit` | Remaining credits per module + total | `completed` (required), `plan_json` (optional) |
| `prereq_check` | Can this course be taken now; missing direct/upstream prerequisites | `course` (required), `completed`, `plan_json` |

`completed` accepts `CS101,CS201`, `CS101:3、CS201`, `CS101(3)` and similar forms
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
        { "kind": "course", "course_code": "CS201" },
        { "kind": "course", "course_code": "CS202" }
      ]}
    }
  ]
}
```

Prerequisite expressions support three node kinds:

- `{"kind": "course", "course_code": "CS201"}` — a specific course must be passed
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

## Credentials

None. The plugin performs all computation in-process and makes **no network calls**,
so there is no API key, endpoint or OAuth configuration step.

## Install

1. Install the `.difypkg` file from this repository release in Dify
   (**Plugins → Install from local package**).
2. Create a tool node, pick **Course path audit**, choose `credit_audit` or `prereq_check`.
3. Optionally wire the JSON output into downstream nodes; both tools emit a Markdown
   summary (text) and a structured JSON message.

## Requirements

- Dify `1.0.0` or newer (tested on `1.17.1`)
- No outbound network access required
- Python runtime is provided by Dify's plugin runner (no manual dependency install)

## Source

Full project (external knowledge-base service, Chatflow DSL, evaluation harness):
see the repository link in this package's marketplace listing / release notes.

## License

Apache-2.0.
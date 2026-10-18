# 课径 · 可核算的培养方案问答

> 2026 第八届全球校园人工智能算法精英大赛 · AI+开源主题赛 参赛项目
> 申报方向：**（一）开源赋能的 AI 应用创新**

把培养方案与教学大纲中的**表格和条款**解析成带先修关系的课程知识图，用自研混合检索 + **确定性**学分/先修核算引擎回答"还差什么、先修满不满足"，并给出可逐项核对的引用与计算过程。

## 边界声明（重要）

- 本项目**不修改、不 fork `langgenius/dify` 任何源码**。全部工作位于 Dify 应用层：
  插件 `.difypkg` / 工作流 DSL / 自研外部 HTTP 服务 / 外部知识库服务 / 文档。
- 部署侧的 `.env`、`docker-compose` 端口、SSRF 代理白名单属**部署配置**，与源码 patch 严格区分，已在报告"合规边界"中声明。
- 仓库内**不含**任何真实培养方案、真实学生数据、密钥。真实语料仅本地演示，仓库只发布脱敏/合成样例。

## 仓库结构

```
.
├─ docs/                     设计与决策文档（先读 00、01、02）
│  ├─ 00-约束清单.md          赛题硬约束 / 工程边界 / 评审隐含偏好 + 可追溯矩阵
│  ├─ 01-参赛计划.md          5 阶段可执行计划（含门禁与风险登记册）
│  └─ 02-环境评估.md          本机可行性结论与部署方案决策
├─ campus-kb/                自研外部服务（FastAPI，本项目实质技术贡献）
│  ├─ app/api/               /retrieval /audit /prereq /health
│  ├─ core/                  parser / schema / graph / index / scoring
│  ├─ data/samples/          脱敏与合成样例语料
│  ├─ eval/                  评测集与结果
│  └─ tests/                 pytest（不依赖 Dify 界面即可跑通）
├─ dify-workflow/            Chatflow 工作流 DSL 导出
├─ dify-plugin/course-audit/ 工具插件（开源交付物，加分项，非晋级门禁）
├─ deploy/                   docker compose / Dockerfile / 部署记录
├─ report/                   技术报告与附件（资源使用清单）
└─ video/                    演示视频脚本与成片
```

## 里程碑（12 个自然日，截止 2026-10-30 20:00）

| 日期 | 阶段 | 出口物 |
|---|---|---|
| 10/18 | 0 赛题解构 | 约束清单 + 仓库骨架 |
| 10/19 | 1 路径决策 | 路径对比与选型结论（C 为主，A 为辅） |
| 10/20–10/21 | 2 技术方案 | 架构、接口契约、度量方案 + **环境 spike 通过** |
| 10/21–10/26 | 3 开发 | 可运行服务 + 工作流 + 评测 + 演示视频素材 |
| 10/27–10/28 | 4 材料 | 技术报告 PDF + 3–5 分钟视频 |
| 10/29–10/30 | 5 预检提交 | 全项 checklist 通过，10/30 12:00 前提交 |

## 当前进度

| 阶段 | 状态 |
|---|---|
| 0 赛题解构 | ✅ 完成（`docs/00-约束清单.md` + 仓库骨架） |
| 1 路径决策 | ✅ 结论已定（C 为主 / A 为辅，见 `docs/01-参赛计划.md` §1）；⏳ 市场取证待补 |
| 2 技术方案 | ✅ 文档完成；⏳ 环境 spike 待做（需 Docker / 云主机） |
| 3 开发 | 🚧 已提前启动：`campus-kb` 完成「表格解析 → 混合检索 → 学分/先修核算 → 评测」闭环，**117 项测试全绿**，评测数字见 `docs/03-评测结果.md`；⏳ Dify 工作流与 Docker 化待做 |

## 快速开始

```bash
cd campus-kb
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
pytest -q                     # 52 passed，无需网络、无需 Dify、无需 API Key
set KB_API_KEY=demo-key
uvicorn app.main:app --port 8088

# 独立验证外部知识库契约（不依赖 Dify 界面）
curl -s http://127.0.0.1:8088/health
curl -s -X POST http://127.0.0.1:8088/retrieval \
  -H "Authorization: Bearer demo-key" -H "Content-Type: application/json" \
  -d '{"knowledge_id":"campus-kb","query":"编译原理的先修要求","retrieval_setting":{"top_k":3,"score_threshold":0.0}}'
```

详见 [campus-kb/README.md](campus-kb/README.md)。

## 许可证

计划以 **Apache-2.0** 发布自研代码（详见 `docs/01-参赛计划.md` §3.5 资源清单）。
第三方组件许可见技术报告附录；**不得引入 AGPL 依赖**（已用 `pdfplumber`(MIT) 规避 PyMuPDF/Ghostscript）。

## 公平性

按赛题附件一第九条，报告、视频、PPT **不含学校名称、校徽、指导教师信息**；仓库同样遵守。

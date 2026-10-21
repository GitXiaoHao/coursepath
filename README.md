# 课径 · 可核算的培养方案问答

仓库：https://github.com/GitXiaoHao/coursepath · 许可证：Apache-2.0（代码） / CC-BY-4.0（文档与样例数据）

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
│  ├─ 02-环境评估.md          本机可行性结论与部署方案决策
│  ├─ 03-评测结果.md          检索/核算/先修三组可复现指标
│  ├─ 04-环境spike记录.md     环境搭建实测记录
│  ├─ 05-系统集成说明.md      Dify 应用层落地方式与集成踩坑
│  ├─ 06-提交预检清单.md      26 项提交前检查（含判定方法与证据）
│  ├─ 07-插件开发与验证.md    插件设计取舍、打包、安装与四个坑
│  └─ 08-插件使用指南.md      把 .difypkg 交给第三方后：怎么装、怎么用、怎么换成自己的方案
├─ campus-kb/                自研外部服务（FastAPI，本项目实质技术贡献）
│  ├─ app/api/               /retrieval /audit /prereq /health
│  ├─ core/                  parser / schema / graph / index / scoring / intent
│  ├─ data/samples/          脱敏与合成样例语料
│  ├─ eval/                  评测集与结果
│  └─ tests/                 pytest（不依赖 Dify 界面即可跑通）
├─ dify-workflow/            Chatflow DSL 生成脚本 + 导出文件
├─ dify-plugin/course-audit/ 工具插件（开源交付物，加分项，非晋级门禁）
├─ deploy/                   docker compose 覆盖文件 / Dockerfile / 部署脚本
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
| 10/29–10/30 | 5 预检提交 | 全项 checklist 已提前全绿，10/30 12:00 前提交并留回执 |

## 当前进度

| 阶段 | 状态 |
|---|---|
| 0 赛题解构 | ✅ 完成（`docs/00-约束清单.md` + 仓库骨架） |
| 1 路径决策 | ✅ 结论已定（C 为主 / A 为辅，见 `docs/01-参赛计划.md` §1）；⏳ 市场取证待补 |
| 2 技术方案 | ✅ 文档完成 + **环境 spike 通过**：Dify 1.17.1 全栈本机跑通，SSRF → 外部知识库链路实测 200（`docs/04`、`docs/05`） |
| 3 开发 | ✅ `campus-kb` 闭环完成（解析→检索→核算→评测），**162 项测试全绿**（演示数据为合成的车辆工程方案：44 门课 / 5 模块 / 160 学分）；Chatflow 三路分流已导入并冒烟通过（检索→条款整理→分流→核算→回答）；`.difypkg` 工具插件已装进 Dify 并端到端跑通（`docs/07-插件开发与验证.md`） |
| 4–5 材料与预检 | ✅ 技术报告 PDF（13 页 / 1.34 MB）、资源清单附件（6 页）、演示视频（3:47 / 9.7 MB）已产出；预检 A1–A8、B1–B8、C1–C7 全绿（`docs/06-提交预检清单.md`）；`deploy/make_submission.py` 一键生成 `提交材料/`；⏳ 待大赛系统正式提交 |

## 端到端现状（本机实测）

```
用户提问 ──► Dify Chatflow ──► 外部知识库 /retrieval（自研混合检索）
                    ├─ 先修类 ──► HTTP /prereq ──► 确定性先修判定 + 条款清单
                    ├─ 条款类 ──► 检索条款摘录（带页码/行号/匹配度）
                    └─ 学分类 ──► HTTP /audit  ──► 分模块缺口核算 + 条款清单
```

示例回答（`EE101,CS101` 已修，「汽车电子控制能选吗？我修过电工电子技术、程序设计基础」）：

> ### 先修检查 · 汽车电子控制（VE205）
> - 结论：**不满足**，缺 1 门直接先修
> - 直接先修（必须先通过）：汽车构造（VE201）：尚未通过
> - **相关条款（检索命中，可逐条核对）** 1. 培养方案 · 课程先修要求表（第 3 页 第 14 行，匹配度 1.00）…

问「我还差多少学分毕业」（已修 `EE101,CS101,VE201`）返回：

> ### 学分核算 · 车辆工程（示范培养方案）
> - 总学分 160，已认定 11，**还差 149 学分**
> - 模块缺口：通识教育 48 / 工程基础 27 / 专业核心课 42 / 专业选修课 16 / 实践教学环节 16

## 快速开始

分两条路，**只验证技术内核不需要 Docker**。命令默认 Windows PowerShell（macOS/Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python` 即可）。

### A. 只跑服务与测试（约 2 分钟，不需要 Docker / Dify）

```powershell
cd <本仓库根目录>
py -3.11 -m venv campus-kb\.venv                  # 3.11 或 3.12 均可
$py = "$PWD\campus-kb\.venv\Scripts\python.exe"   # 后面都用它
& $py -m pip install -r campus-kb\requirements.txt
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"   # 否则中文输出乱码

Push-Location campus-kb
& $py -m pytest -q                 # 162 passed（无网络、无 Dify、无 API Key）
& $py -m eval.run_eval             # 评测带门禁：Recall@1<0.8 或 exact_match<1.0 即非零退出
Pop-Location
```

起服务并验契约（新开一个终端，先重复上面两行环境变量与 `$py`）：

```powershell
cd <本仓库根目录>\campus-kb
$env:KB_API_KEY = "demo-key"
& $py -m uvicorn app.main:app --port 8088

# 另开终端，先重复 $py 与环境变量
curl.exe -s http://127.0.0.1:8088/health
& $py -c "import json,urllib.request as u; req=u.Request('http://127.0.0.1:8088/retrieval',data=json.dumps({'knowledge_id':'campus-kb','query':'汽车设计需要先修哪些课程','retrieval_setting':{'top_k':3,'score_threshold':0.0}}).encode(),headers={'Authorization':'Bearer demo-key','Content-Type':'application/json'}); d=json.load(u.urlopen(req)); print(len(d['records']), d['records'][0]['metadata']['page'])"
```

> 故意的：PowerShell 5.1 会把原生 `curl.exe` 参数里的双引号吃掉，`-d '{...}'` 会变成非法 JSON；这里用 Python 发请求最稳（bash 下可直接用 `curl -d`）。

### B. 全栈跑通（Dify 1.17.1 + 自研服务 + 插件）

前置：Docker Desktop 已启动（脚本会自己把 Docker 加进 PATH）；能拉取 Docker Hub 镜像（拉取失败先看 `docs/04` 的镜像加速说明）。

```powershell
cd <本仓库根目录>
.\deploy\bootstrap.ps1        # 幂等：起 Dify 全栈 + campus-kb，自动生成 .env 与随机 KB_API_KEY

# 首次使用：浏览器打开 http://localhost 创建管理员
#   邮箱建议填 admin@campus-kb.local（用别的邮箱就设 $env:DIFY_ADMIN_EMAIL）
$env:DIFY_ADMIN_EMAIL    = "admin@campus-kb.local"
$env:DIFY_ADMIN_PASSWORD = "<刚才设置的密码>"

& $py -m pip install -r requirements-tools.txt    # 下面三个脚本需要 pyyaml / httpx
& $py deploy\spike_dify.py                        # ① 外部知识库 → SSRF → campus-kb 链路
& $py dify-workflow\build_chatflow.py --import    # ② 导入 Chatflow、发布、跑冒烟问答
& $py deploy\install_plugin.py                    # ③ 安装 .difypkg 并验证工具调用
```

跑完在 Dify 的 **WEB APPS** 里就有「课径 · 可核算的培养方案问答」，直接提问即可。
健康检查：`http://localhost/`（Dify）、`http://127.0.0.1:8088/health`（自研服务）。

> 三个脚本都会打印断言结果并以退出码表示成败；重跑 `build_chatflow.py --import` 会先删同名应用再导入（每次生成新 app_id 与 API Key）。
> 常见坑见 `docs/04-环境spike记录.md` 与 `docs/05-系统集成说明.md`：Docker CLI 不在 PATH、镜像 `magic number mismatch` 重试即可、`local_sandbox` 的 `--require-auth-token`、外部知识库 `retrieval_model` 为 NULL 导致检索静默返回 0 条、控制台接口连 GET 也要 `X-CSRF-Token`。

### C. 重新生成材料（可选，需要本机 Edge）

```powershell
& $py report\build_pdf.py                 # 技术报告 PDF（13 页）
& $py report\build_annex.py               # 资源清单附件 PDF（6 页）
& $py video\make_slides.py                # 幻灯片 PNG
& $py video\record_demo.py                # 录制真实界面（Playwright 驱动本机 Edge）
& $py video\build_video.py                # 合成旁白与成片
& $py deploy\make_submission.py           # 收拢到 提交材料/ 并做规格校验
```

> `record_demo.py` / `make_figures.py` 复用系统 Edge（`channel="msedge"`），不需要 `playwright install` 下载浏览器；密码与邮箱从 `DIFY_ADMIN_PASSWORD` / `DIFY_ADMIN_EMAIL` 读取。

> 常见坑都记在 `docs/04-环境spike记录.md` 与 `docs/05-系统集成说明.md`：Docker CLI 不在 PATH、镜像拉取 `magic number mismatch` 重试即可、`local_sandbox` 的 `--require-auth-token`、外部知识库 `retrieval_model` 为 NULL 导致检索静默返回 0 条、控制台接口连 GET 也要 `X-CSRF-Token`。

### D. 只把插件分发给别人（不跑本项目的服务）

把 `dify-plugin/dist/course_audit-0.2.1.difypkg`（24.6 KB，自带核算内核、无网络调用）发给对方，对方：

1. Dify 左侧 **集成** → 左上 **安装** → 下拉选 **本地集成包** → 选 `.difypkg`；
2. 集成 → **工具** 里出现「课径核算」即装好；
3. 工作室新建 Chatflow → 加 **工具** 节点 → 选 `credit_audit` / `prereq_check` → 参数 `completed=EE101,CS101`（`plan_json` 留空用内置示例方案）。

自建包没有官方签名，若报签名校验失败，需在**部署侧** `.env` 设 `FORCE_VERIFYING_SIGNATURE=false` 并重启 `api worker plugin_daemon`（不改源码）。

完整步骤、期望输出、`plan_json` 写法与 5 条常见问题：[docs/08-插件使用指南.md](docs/08-插件使用指南.md)。

详见 [campus-kb/README.md](campus-kb/README.md) 与 [docs/05-系统集成说明.md](docs/05-系统集成说明.md)。

## 许可证

自研代码以 **Apache-2.0** 发布（根目录 `LICENSE`），文档与样例数据以 **CC-BY-4.0** 发布；清单见 `report/开源及第三方资源使用清单.md`。
第三方组件许可见技术报告附录；**不得引入 AGPL 依赖**（已用 `pdfplumber`(MIT) 规避 PyMuPDF/Ghostscript）。

## 公平性

按赛题附件一第九条，报告、视频、PPT **不含学校名称、校徽、指导教师信息**；仓库同样遵守。

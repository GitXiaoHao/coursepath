# 课径 · 可核算的培养方案问答

仓库：https://github.com/GitXiaoHao/coursepath · 许可证：Apache-2.0

把培养方案与教学大纲中的**表格和条款**解析成带先修关系的课程知识图，用自研混合检索 + **确定性**学分/先修核算引擎回答"还差什么、先修满不满足"，并给出可逐项核对的引用与计算过程。

> 本仓库只包含**可运行的代码、部署脚本与测试**。设计文档、技术报告、演示视频、提交材料与流程图仅在本地留存，不随仓库发布。

## 仓库结构

```
.
├─ campus-kb/            自研外部知识服务（FastAPI，本项目核心）
│  ├─ app/               入口与配置
│  │  ├─ main.py         create_app() / 统一错误体 / 启动装载语料
│  │  ├─ settings.py     全部配置来自 KB_* 环境变量
│  │  ├─ profile_store.py 学生课程档案（SQLite，可选）
│  │  └─ api/
│  │     ├─ retrieval.py   POST /retrieval            外部知识库契约（Dify 调用）
│  │     ├─ course.py      POST /audit /prereq /course-info 确定性核算与先修判定
│  │     ├─ planner.py     POST /study-plan           按档案生成可修候选
│  │     └─ profile.py     POST /profile-summary /profile-command  档案查看与增删
│  ├─ core/              纯 Python 内核，可脱离服务单独调用
│  │     parser · schema · graph · index · filters · scoring · intent · corpus · plan_catalog · embeddings
│  ├─ data/samples/      合成培养方案样例（plan.json / plan-demo.md / chunks.jsonl）
│  ├─ eval/              评测用例与 run_eval.py（带门禁，指标不达标即非零退出）
│  ├─ scripts/           export_plan_markdown.py（方案 ↔ Markdown 双向校验）
│  └─ tests/             pytest 套件（不依赖 Dify、不联网、不需要 API Key）
├─ dify-workflow/        Chatflow DSL 生成脚本与导出 YAML
│  ├─ build_chatflow.py  生成并可选导入 DSL（--v2 / --smart / --intent / --import）
│  ├─ build_plugin_demo.py 生成"插件工具节点"最小工作流
│  └─ *.chatflow.yml / *.workflow.yml  已导出的 DSL 存档
├─ dify-plugin/          工具插件（独立分包，内核随包复制，不调用本服务）
│  ├─ course-audit/      manifest / provider / tools（credit_audit、prereq_check、study_plan、course_info）
│  ├─ dist/              course_audit-0.2.2.difypkg（可直接安装）
│  ├─ pack.py            打包 .difypkg
│  └─ sync_core.py sync_plan.py  从 campus-kb 同步内核与示例方案
├─ deploy/               compose 覆盖、部署与验证脚本
│  ├─ bootstrap.ps1      幂等起 Dify 全栈 + campus-kb（不改 Dify 源码）
│  ├─ dify.override.yaml compose 覆盖文件
│  ├─ spike_dify.py      外部知识库 → SSRF → campus-kb 链路验证
│  └─ install_plugin.py  安装 .difypkg 并验证工具调用
├─ .gitignore / .gitattributes
├─ LICENSE               Apache-2.0
├─ README.md
└─ requirements-tools.txt  deploy/ 与 dify-workflow/ 脚本所需依赖（pyyaml、httpx 等）
```

## 端到端现状（本机实测）

```
用户提问 ──► Dify Chatflow ──► 外部知识库 /retrieval（自研混合检索）
                    ├─ 先修类 ──► /prereq       ──► 先修表达式树求值 + 条款清单
                    ├─ 学分类 ──► /audit        ──► 分模块缺口核算 + 条款清单
                    ├─ 课程信息 ─► /course-info  ──► 模块、学分、建议开课学期
                    ├─ 规划类 ──► /study-plan   ──► 可修候选与建议顺序
                    └─ 条款类 ──► 检索条款摘录（带页码 / 行号 / 匹配度）
```

示例回答（档案已修 `EE101,CS101,VE201`，「汽车电子控制能选吗？」）：

> ### 先修检查 · 汽车电子控制（VE205）
> - 结论：**可以选**（直接先修已全部满足）
> - 判定依据：先修关系来自培养方案/教学大纲登记，逐条可回溯，非模型生成
>
> **相关条款（检索命中，可逐条核对）**
> 1. 车辆工程（示范培养方案）· 课程目录（第 8 页 第 6 行，匹配度 1.00）：汽车电子控制（VE205）：4 学分，所属专业核心课；先修要求：电工电子技术（EE101）且程序设计基础（CS101）且汽车构造（VE201）…

## 快速开始

**只验证技术内核不需要 Docker。** 命令默认 Windows PowerShell（macOS/Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python` 即可）。

### A. 只跑服务与测试（约 2 分钟，不需要 Docker / Dify）

```powershell
cd <本仓库根目录>
py -3.11 -m venv campus-kb\.venv                  # 3.11 或 3.12 均可
$py = "$PWD\campus-kb\.venv\Scripts\python.exe"   # 后面都用它
& $py -m pip install -r campus-kb\requirements.txt
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"   # 否则中文输出乱码

Push-Location campus-kb
& $py -m pytest -q                 # 单元与契约测试（无网络、无 Dify、无 API Key）
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
& $py -c "import json,urllib.request as u; req=u.Request('http://127.0.0.1:8088/retrieval',data=json.dumps({'knowledge_id':'campus-kb','query':'汽车电子控制的先修要求','retrieval_setting':{'top_k':3,'score_threshold':0.0}}).encode(),headers={'Authorization':'Bearer demo-key','Content-Type':'application/json'}); d=json.load(u.urlopen(req)); print(len(d['records']), d['records'][0]['metadata']['page'])"
```

> 故意的：PowerShell 5.1 会把原生 `curl.exe` 参数里的双引号吃掉，`-d '{...}'` 会变成非法 JSON；这里用 Python 发请求最稳（bash 下可直接用 `curl -d`）。

### B. 全栈跑通（Dify 1.17.1 + 自研服务 + 插件）

前置：Docker Desktop 已启动（脚本会自己把 Docker 加进 PATH）；能拉取 Docker Hub 镜像。

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
> 常用参数：`--v2`（单路由，避免误触发核算）、`--smart`（叠加表达层）、`--intent`（叠加意图识别与课程代码提取，需配合 `--smart`）、`--keep-existing`（保留同名旧应用）。

### C. 只把插件分发给别人（不跑本项目的服务）

把 `dify-plugin/dist/course_audit-0.2.2.difypkg`（约 32 KB，自带核算内核、无网络调用）发给对方，对方：

1. Dify 左侧 **集成** → 左上 **安装** → 下拉选 **本地集成包** → 选 `.difypkg`；
2. 集成 → **工具** 里出现「课径核算」即装好；
3. 工作室新建 Chatflow → 加 **工具** 节点 → 选 `credit_audit` / `prereq_check` / `study_plan` / `course_info` → 参数示例 `completed=EE101,CS101`（`plan_json` 留空则用包内示例方案）。

自建包没有官方签名，若报签名校验失败，需在**部署侧** `.env` 设 `FORCE_VERIFYING_SIGNATURE=false` 并重启 `api worker plugin_daemon`（不改源码）。

详见 [dify-plugin/course-audit/README.md](dify-plugin/course-audit/README.md)。

## 常见问题（部署与集成踩坑）

1. **Docker CLI 不在 PATH**：脚本已显式加入 `C:\Program Files\Docker\Docker\resources\bin`；手动调用需自行设置。
2. **镜像拉取报 `magic number mismatch`**：分层下载的临时网络错误，重试即可；国内网络建议配镜像加速。
3. **`local_sandbox` 反复重启**：Dify 1.17.1 下需在 compose 覆盖文件里给它补 `--require-auth-token` 等启动参数（`deploy/dify.override.yaml` 已处理）。
4. **外部知识库检索静默返回 0 条**：dataset 的 `retrieval_model` 为 `NULL` 会被跳过，登记外部知识库时必须带 `external_retrieval_model`；返回体里 `metadata` 必须是对象，`null` 会让检索管线报错。
5. **控制台接口连 GET 也要 `X-CSRF-Token`**：脚本统一先登录拿 cookie 与 CSRF，再发请求。
6. **Dify 出站被 SSRF 代理拦截**：用官方环境变量 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=campus-kb` 放行同网段服务，不需改 squid 模板。

详见 [campus-kb/README.md](campus-kb/README.md)。

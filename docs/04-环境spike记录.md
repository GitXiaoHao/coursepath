# 环境 spike 记录（阶段 2 门禁 G2 证据）

> 目的：证明「零源码修改」前提下，Dify 1.17.1 能真实调用自研 `campus-kb` 服务。
> 结论：**通过**。链路 Dify → Squid SSRF Proxy → campus-kb `/retrieval` 实测打通。

## 1. 环境事实

| 项 | 实测值 |
| --- | --- |
| 主机 | Windows 11 家庭版 10.0.26300，i5-1240P（12 核 16 线程），内存 31.6 GB（空闲约 9 GB） |
| GPU | Intel Iris Xe，无 CUDA |
| Docker | Docker Desktop 4.94（WSL2 后端），Engine 29.8.2，Compose v5.5.1 |
| Docker 资源 | 16 CPU / 15.4 GB 内存（容器级），位于 C: 盘 |
| Python | 3.15.0（`K:\Python3.15`）+ 3.12.15（项目私有，`.tools/python`） |
| 磁盘 | C: 空闲 19.1 GB（Dify 镜像占大头），M: 空闲 66.8 GB |

Docker CLI 不在 PATH，需 `C:\Program Files\Docker\Docker\resources\bin`。
镜像拉取依赖国内镜像源（配置在 `C:\Users\yuhao\.docker\daemon.json`，
`docker.m.daocloud.io` / `docker.1panel.live` / `hub.rat.dev` / `docker.nju.edu.cn`），
直连 `registry-1.docker.io` 会超时。

## 2. 部署方式（关键：零源码修改）

```text
deploy/
├── bootstrap.ps1          # 一键：克隆官方 docker 目录 → 生成 .env（含强随机密钥）→ 起栈
├── dify.override.yaml     # compose 覆盖文件，由 bootstrap 复制为 docker-compose.override.yaml
├── spike_dify.py          # 本记录第 4 节的自动化验收脚本
└── dify/                  # 官方仓库 clone（.gitignore 排除）
```

官方 `docker-compose.yaml`、`squid.conf.template`、`.env.example` **一个字节都没有改**。
所有定制通过三种官方支持机制实现：

1. `docker-compose.override.yaml`（Compose 规范自动合并）——加入 `campus-kb` 服务、双网络挂载。
2. `.env` 变量 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=campus-kb`——官方 ssrf_proxy entrypoint
   会据此生成 `dify_allow_private.conf`，插在 `http_access deny to_private_networks` **之前**，
   因此内网容器名被放行，**无需编辑 squid 模板**。
3. 控制台/API 注册——外部知识库 API 走平台原生功能，不是插件。

### 2.1 已修的两处官方编排缺陷

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `local_sandbox` 无限重启 | 1.17.1 的 compose 传入 `--require-auth-token`，同版本镜像 `shellctl` 不认识（`flag provided but not defined`） | override 里覆盖 `command`，去掉该参数；本项目不使用 Agent 代码沙箱 |
| `docker compose` 报 `error getting credentials` | 登录凭证助手 `docker-credential-desktop` 不在 PATH | 调用前把 Docker 的 `resources\bin` 加进 PATH |

## 3. 服务清单

`docker compose ps` 全部 healthy：`api` `worker` `worker_beat` `db` `redis` `nginx`
`weaviate` `sandbox` `ssrf_proxy` `plugin_daemon` `local_sandbox` `campus-kb`。

- Dify 控制台：`http://localhost`（管理员 `admin@campus-kb.local`，密码见 `.explore/`，不入库）
- campus-kb：容器内 `http://campus-kb:8080`，宿主 `http://127.0.0.1:8088`（仅回环）

## 4. 验收结果（`python deploy/spike_dify.py`）

```text
[1/4] 登录成功 admin@campus-kb.local（csrf 已获取）
[2/4] 登记外部知识库 API a0fc1add-... -> http://campus-kb:8080
[3/4] 绑定外部知识库 85cd6c41-...
[4/4] 命中测试通过，返回 3 条（query=编译原理的先修要求是什么）
      1. score=1.0000 培养方案 2026 · 课程先修要求表 | doc=plan-2026 p14
      2. score=0.9375 课程大纲 · 编译原理 | doc=syllabus-compiler p1
      3. score=0.4513 培养方案 2026 · 专业核心课模块 | doc=plan-2026 p12
SPIKE PASS：Dify -> SSRF Proxy -> campus-kb 链路打通
```

这一步同时验证了赛题文档里最容易踩的三个契约点：

1. `endpoint` 只写到主机端口，**`/retrieval` 由 Dify 追加**；
2. `metadata` 必须返回对象，返回 `null` 会让 Dify 检索管线报错；
3. 无命中返回 `{"records": []}` 而不是 404。

## 5. 门禁 G2 判定

| 判定项 | 结果 |
| --- | --- |
| Dify 1.17.1 全栈可启动 | ✅ 12 个服务 healthy |
| 外部知识库端到端可检索 | ✅ 3 条命中，Top-1 正确，元数据完整 |
| 出站经 SSRF Proxy 放行 | ✅ 未改 squid 模板，仅用官方 `.env` 开关 |
| Dify 源码/官方编排零修改 | ✅ 仅 override + .env + 控制台注册 |
| 复现成本 | ✅ `bootstrap.ps1` + `spike_dify.py` 两条命令 |

## 6. 仍待验证（G2 之外的 G3 项）

- Chatflow 中 HTTP 请求节点调用 `/audit`、`/prereq`（需先导入工作流 DSL）
- `.difypkg` 打包与 `plugin_daemon` 安装（需 dify-plugin-daemon CLI，v0.6.11）

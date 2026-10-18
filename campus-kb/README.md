# campus-kb · 自研外部知识库服务

把培养方案/教学大纲的**表格与条款**转成可检索、可核算的结构化知识，并以 Dify
External Knowledge API 契约对外提供服务。**不修改、不 fork Dify 源码。**

## 为什么单独做服务

Dify 内置知识库是"分段 + 单路向量检索"，两个能力缺口直接决定本项目的存在价值：

1. **跨页表格语义**：先修要求写在表格里，通用分段会把一行拆散，失去"课程—先修—页行号"的对应关系；
2. **结构化过滤**：Dify UI 无法配置 `metadata_condition`，需要服务端实现按 `module` / `course_code` / `page` 过滤。

## 快速开始

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt

# 1) 跑测试（无需网络、无需 Dify、无需 API Key）
pytest -q          # 52 passed

# 2) 起服务
set KB_API_KEY=demo-key
uvicorn app.main:app --host 127.0.0.1 --port 8088

# 3) 验证契约
curl -s http://127.0.0.1:8088/health
curl -s -X POST http://127.0.0.1:8088/retrieval \
  -H "Authorization: Bearer demo-key" -H "Content-Type: application/json" \
  -d "{\"knowledge_id\":\"campus-kb\",\"query\":\"编译原理的先修要求\",\"retrieval_setting\":{\"top_k\":3,\"score_threshold\":0.0}}"
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `KB_API_KEY` | `dev-key` | Bearer 令牌；**演示环境必须显式设置** |
| `KB_KNOWLEDGE_ID` | `campus-kb` | 登记到 Dify 的 knowledge_id |
| `KB_STRICT_KNOWLEDGE_ID` | `0` | 置 `1` 后 ID 不匹配返回 `1002`；默认宽松只告警（避免配置笔误在 Dify 侧变 500） |
| `KB_CORPUS` | `data/samples/chunks.jsonl` | 语料路径 |
| `KB_FUSION` | `weighted` | `weighted` \| `rrf`，用于实验对比 |
| `KB_ALPHA` | `0.5` | 稠密通道权重 |
| `KB_EMBED_BASE_URL` / `KB_EMBED_API_KEY` / `KB_EMBED_MODEL` | 空 / 空 / `BAAI/bge-m3` | 留空则关闭稠密通道，退化为纯 BM25 |

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/retrieval` | Dify 外部知识库契约（Dify 会在登记的 endpoint 后自动追加该路径） |
| GET | `/health` | 索引规模、向量后端、融合策略 |
| GET | `/openapi.json` | 自动生成，报告"可独立验证"的证据 |

响应固定为 `{"records":[{"content","score","title","metadata"}]}`，**`metadata` 永远是对象**（`null` 会让 Dify 检索管线报错）；无结果返回 `{"records": []}` 而非 404。

## 目录

```
core/schema.py       课程/学分/先修表达式/来源引用 领域模型
core/filters.py      metadata_condition 算子实现（and/or + 16 个算子）
core/index.py        BM25 + 稠密 + 融合 + 过滤 + 证据门槛
core/embeddings.py   向量后端（NullEmbedder 离线可用 / OpenAI 兼容）
core/corpus.py       JSONL 语料装载与元数据扁平化
app/api/retrieval.py 契约端点与错误体
app/main.py          应用工厂、/health、统一错误处理
```

## 实现要点（评审可核对）

1. **BM25 用 `BM25Plus`**：idf = log((N+1)/df) 恒非负，避免小语料下"学分"这类高频词的负分把相关分块误杀。
2. **证据门槛**：零词法/语义重叠的分块直接剔除，否则归一化得分为 0 而 Dify 默认
   `score_threshold=0.0` 会让无关分块全量通过、污染答案。
3. **过滤先行**：`metadata_condition` 在打分前裁剪候选集，不满足条件的分块没有打分资格。
4. **默认宽松的 knowledge_id 校验**：配置笔误由服务端兜底，不让 Dify 用户看到 500。

## 待办（后续阶段）

- [ ] `core/parser.py`：pdfplumber 表格解析 + 条款切分（10/21）
- [ ] `core/graph.py` / `core/scoring.py`：先修链与学分缺口核算、`/audit`、`/prereq`（10/23）
- [ ] `eval/`：60 题检索集、30 个核算案例、与 Dify 内置知识库的对比实验（10/22–10/25）
- [ ] `Dockerfile` + compose（10/24）

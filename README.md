# 机械智能制造统一工作台

这是从 `machining_process_rag1` 和 `step_model_retrieval` 派生出的独立融合项目。两个源项目保持原状；本项目沿用 `machining_process_rag1` 的工业风 Streamlit UI，并采用 `step_model_retrieval` 中更完善的 CAD、混合检索、多模态和企业资料问答实现。

当前定位是**纯检索系统**：提供模型检索与企业资料问答两个工作区，不生成加工工艺方案。

## 功能区

### 模型检索

- STEP → OCP/XCAF 几何事实 → 可解释加权相似检索；
- STEP 或中文描述 → BGE + Chroma 语义召回；
- 中文描述 → BGE、BM25 与工程类别知识融合排序；
- 图片 → CLIP 或离线轮廓指纹检索；
- 可选 CLIP 统一文字/图片/STEP 多模态补充召回；
- 几何结果附带知识图谱邻域，导入事实与几何候选分开展示；
- 查询模型与候选模型均可进行三维预览。

### 企业资料问答

- 检索企业 STEP、装配 BOM 和工程图 PDF；
- 图号精确匹配优先于泛化语义匹配；
- 严谨知识库模式只依据资料回答；
- AI 助手模式允许通用建议，但必须与企业事实明确区分；
- 回答保留 `[S#]` 证据编号和本地来源文件。

## 检索数据边界

三类向量空间保持独立，避免不同含义的分数相互污染：

- `data/vector_stores/cad_semantic`：CAD 中文工程语义；
- `data/vector_stores/enterprise`：STEP、BOM 与工程图资料；
- `data/vector_stores/multimodal`：CLIP 统一多模态原型。

它们通过 `part_id`、`model_group_id`、`source_file` 和来源类型关联，而不是直接合并集合。

## 代码与数据结构

业务代码集中在 `machining_unified/` 包中：`cad/` 处理 STEP 几何，`retrieval/` 处理 RAG 与多模态召回，`knowledge/` 处理知识和证据，`services/` 负责页面用例，`storage/` 管理本地状态，`ui/` 保留主项目界面。根目录只保留 Streamlit 启动文件 `app.py`。

数据按职责放在 `data/catalogs`、`data/knowledge`、`data/enterprise`、`data/vector_stores` 和 `data/runtime`。完整说明见 `data/README.md`。

克隆本仓库后不会附带企业资料和向量库（均已 gitignore）。需要自行准备 `data/enterprise/cad_samples/` 下的 STEP 模型，再按下节顺序重建索引，否则页面会提示向量库不存在。

## 运行环境

建议使用 Python 3.12：

```powershell
cd C:\Users\w\Desktop\machining_unified_ai
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8503
```

浏览器访问 `http://localhost:8503`。

本地 `.env` 支持以下配置：

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

日志相关环境变量（均为可选，有默认值）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | `INFO` | 取值非法时自动回退到 `INFO` |
| `LOG_FORMAT` | `json` | `json` 为单行结构化日志；`text` 便于本地直读 |
| `LOG_FILE` | 空 | 留空输出到 stderr；指定路径则写文件并自动创建父目录，不可写时回退 stderr 且不阻断启动 |

JSON 日志刻意使用 ASCII 转义（中文形如 `文字`）。日志要经过 stderr、容器运行时和采集器多段管道，任一段用非 UTF-8 解码都会产生乱码；转义后任何 JSON 解析器都能还原原文。

`.env`、企业资料、STEP 文件、聊天历史和向量库均已加入 `.gitignore`。

## 重建索引

停止 Streamlit 后依次执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_cad_catalog.py
.\.venv\Scripts\python.exe scripts\build_vector_index.py
.\.venv\Scripts\python.exe scripts\build_enterprise_kb.py
.\.venv\Scripts\python.exe scripts\build_unified_index.py
```

第一步会顺带从 BOM 与人工标注清单回填设计属性并重算检索文本，因此后面三个索引必须一起重建。统一多模态索引需要本机已有 CLIP 模型，或设置 `ALLOW_CLIP_DOWNLOAD=1` 允许首次下载。

重建后可执行只读完整性检查：

```powershell
.\.venv\Scripts\python.exe scripts\check_databases.py
```

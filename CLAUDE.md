# machining_unified_ai 接手指南

> 面向新接手本项目的 Claude/Codex/开发人员。请先阅读本文件，再修改代码或数据库。

## 1. 30 秒了解项目

这是一个独立的机械智能制造融合项目：

- 前端 UI 继承自 `machining_process_rag1`；
- STEP 几何解析、模型检索、混合 RAG 和企业资料问答主要吸收自 `step_model_retrieval`；
- 两个源项目仅作为历史来源保留，日常开发只修改本项目；
- 主入口是根目录 `app.py`，业务实现全部放在 `machining_unified/` 包内；
- 页面提供三个相互独立的工作区：工艺推荐、模型检索、企业资料问答；
- 数据库不是一个库，而是四套用途和向量空间不同的 Chroma 库。

项目目录（当前主副本，日常开发只改这里）：

```text
C:\Users\w\Desktop\machining_unified_ai
```

`C:\Users\w\PycharmProjects\machining_ai_workspace\machining_unified_ai` 是同内容的旧副本，不再维护；不要在那里改代码。

只读历史源项目：

```text
C:\Users\w\PycharmProjects\machining_ai_workspace\machining_process_rag1
C:\Users\w\PycharmProjects\machining_ai_workspace\step_model_retrieval
```

除非用户明确要求，不要修改、移动或删除这两个源项目。

## 2. 必须遵守的设计边界

1. **UI 以当前融合项目为准。** 不要重新复制源项目页面覆盖 `app.py` 或 `machining_unified/ui/`。
2. **业务代码只放在 `machining_unified/`。** 根目录除 `app.py` 外不要再次创建业务模块副本。
3. **数据路径只能从 `machining_unified/config/paths.py` 引用。** 不要在业务代码中拼接 `data/...` 硬编码路径。
4. **四套向量库保持独立。** 它们的嵌入模型、内容和分数含义不同，不能直接合并分数或集合。
5. **检索证据与模型推断必须区分。** 几何事实、语义相似度、多模态相似度和 LLM 建议不可伪装成同一种证据。
6. **企业资料回答必须可追溯。** 严谨知识库模式只使用企业资料，引用保留 `[S#]` 和来源文件。
7. **生产使用前需要人工复核。** 工艺参数、几何候选、OCR 结果和装配关联都不能被描述为已自动确认的生产事实。
8. **不要提交秘密或本地大文件。** `.env`、向量库、聊天历史、企业 STEP/BOM/图纸和本地模型已由 `.gitignore` 排除。

## 3. 入口与请求流

`app.py` 只负责页面编排、会话状态和调用服务层。

```text
Streamlit app.py
├─ 工艺推荐
│  └─ services/process.py
│     ├─ knowledge/drawing.py          二维图纸文字层/OCR
│     ├─ cad/extraction.py             STEP 几何事实
│     ├─ Chroma process                工艺案例召回
│     ├─ knowledge/safety.py           缺失/未覆盖要求拦截
│     └─ DeepSeek                      特征提取与工艺回答
├─ 模型检索
│  └─ services/model_search.py
│     ├─ cad/retrieval.py              可解释几何相似度
│     ├─ retrieval/cad_rag.py          BGE 中文语义召回
│     ├─ knowledge/engineering.py      BM25/类别路由/混合排序
│     ├─ cad/visual.py                 图片检索
│     └─ retrieval/multimodal.py       可选 CLIP 统一补充召回
└─ 企业资料问答
   └─ knowledge/enterprise.py
      ├─ 图号精确匹配与 BM25
      ├─ Chroma enterprise
      └─ DeepSeek 严谨回答/助手回答
```

页面组件位于：

- `machining_unified/ui/components.py`：三个工作区的主要页面组件；
- `machining_unified/ui/retrieval_components.py`：检索结果和证据展示；
- `machining_unified/ui/styles.py`：加载工业风 CSS；
- `assets/industrial.css`：主样式；
- `.streamlit/config.toml`：Streamlit 深色主题。

## 4. Python 包职责

| 目录 | 职责 | 常用入口 |
|---|---|---|
| `machining_unified/cad/` | STEP 解析、几何特征、相似度、3D 预览、视觉检索 | `extract_step_features`、`retrieve_similar_cad`、`render_step_file` |
| `machining_unified/retrieval/` | BGE/Chroma CAD RAG 与 CLIP 多模态检索 | `retrieve_cad_rag_by_text`、`retrieve_unified_by_*` |
| `machining_unified/knowledge/` | 工程知识、企业证据、图纸、规则、清单和安全约束 | `hierarchical_retrieve`、`retrieve_enterprise_knowledge` |
| `machining_unified/services/` | 页面用例编排，隔离 UI 与底层实现 | `recommend_process`、`search_by_text`、`search_by_step` |
| `machining_unified/storage/` | 聊天历史与数据库注册表 | `chat_history.py`、`database_registry.py` |
| `machining_unified/config/` | 全项目路径配置 | `paths.py` |
| `machining_unified/ui/` | Streamlit 组件与样式接入 | `components.py`、`retrieval_components.py` |

新增功能时，优先放入对应包，再由 `services/` 编排，最后由 `app.py` 调用。不要让 UI 直接承担索引、文件解析或检索算法。

## 5. 数据目录

所有路径的唯一注册表：`machining_unified/config/paths.py`。

```text
data/
├─ catalogs/                 CAD 目录、去重清单、part_id 跨模态清单
├─ knowledge/                工艺案例、规则、设备、刀具、来源文档、模板
├─ enterprise/
│  ├─ cad_samples/           STEP/STP 原始模型
│  └─ assembly_packages/     装配包、BOM、工程图和装配清单
├─ vector_stores/            四套 Chroma 持久化库
├─ runtime/                  聊天历史等运行数据
└─ models/                   OCR 等本地模型
```

重要 JSON：

- `data/catalogs/cad_models.json`：可检索 CAD 主目录，包含几何特征和来源路径；
- `data/catalogs/cad_duplicates.json`：按 MD5 识别的重复 STEP 文件；
- `data/catalogs/part_manifest.json`：文字案例、CAD 和其他模态之间的 `part_id` 关联；
- `data/catalogs/unified_multimodal_manifest.json`：多模态索引构建信息；
- `data/knowledge/process_cases.json`：基础工艺案例；
- `data/knowledge/process_extensions.json`：扩展工艺案例；
- `data/runtime/chat_history.json`：企业资料问答历史。

## 6. 四套向量数据库

数据库注册表位于 `machining_unified/storage/database_registry.py`。

| 键 | 目录 | Chroma collection | 当前记录数 | 用途 |
|---|---|---|---:|---|
| `process` | `data/vector_stores/process` | `machining_cases` | 58 | 工艺案例、来源文档和 CAD 特征 |
| `cad_semantic` | `data/vector_stores/cad_semantic` | `cad_models` | 24 | CAD 中文工程语义 |
| `enterprise` | `data/vector_stores/enterprise` | `enterprise_knowledge` | 66 | STEP、BOM 和工程图证据 |
| `multimodal` | `data/vector_stores/multimodal` | `unified_cad_models` | 24 | CLIP 文字/图片/STEP 表征 |

以上是 2026-08-13 的已验证基线，不应在业务代码中硬编码这些数量。数据导入后数量可以变化，应以完整性检查结果为准。

为什么不能合并：

- `process` 和 `cad_semantic` 使用 BGE 中文文本嵌入，但文档语义和筛选字段不同；
- `enterprise` 是企业证据单元，图号精确命中会优先于泛化向量相似度；
- `multimodal` 使用 512 维 CLIP 表征，分数不能和 BGE/几何分数直接比较；
- STEP 几何相似度是代码计算的可解释加权分数，也不能伪装成向量相似度。

## 7. 本地运行

当前项目使用 Python 3.12，已存在独立 `.venv`。

```powershell
cd C:\Users\w\Desktop\machining_unified_ai
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8503
```

浏览器地址：`http://localhost:8503`

本地 `.env` 变量名：

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

不要读取、打印、提交或复制真实密钥。没有 DeepSeek 密钥时，本地目录检查和大部分检索仍可测试，但需要 LLM 的工艺生成、解释和问答功能不可完整运行。

首次使用 BGE 或 CLIP 时可能需要从 Hugging Face 下载模型；离线环境需要预先缓存模型。当前实现默认在 CPU 上运行。

## 8. 数据更新与索引重建

### 导入企业装配包

输入目录需要包含且仅包含一个装配 STEP，并带有约定目录下的 BOM/工程图：

```powershell
.\.venv\Scripts\python.exe scripts\import_assembly_package.py <资料包目录>
```

导入脚本会把审计副本放入 `data/enterprise/assembly_packages/`，把装配 STEP 放入 `data/enterprise/cad_samples/assemblies/`。

### 完整重建顺序

重建前先停止 Streamlit，并关闭可能占用 `chroma.sqlite3` 的数据库工具。

```powershell
.\.venv\Scripts\python.exe scripts\build_cad_catalog.py
.\.venv\Scripts\python.exe scripts\build_vector_index.py
.\.venv\Scripts\python.exe scripts\build_enterprise_kb.py
.\.venv\Scripts\python.exe scripts\build_unified_index.py
.\.venv\Scripts\python.exe scripts\build_full_knowledge_base.py
.\.venv\Scripts\python.exe scripts\check_databases.py
```

顺序含义：

1. 扫描 STEP，提取 OCP/XCAF 几何并生成 CAD 目录；
2. 根据 CAD 目录重建 BGE CAD 语义库；
3. 根据 STEP、BOM 和工程图重建企业证据库；
4. 根据 CAD 目录重建 CLIP 多模态库；
5. 根据工艺案例、来源文档和 CAD 目录重建工艺知识库；
6. 只读核对 JSON、来源文件、collection 和记录数。

不要只移动数据目录而不重建索引，因为 Chroma metadata 中保存了 `source_file`，旧路径会导致证据链接失效。

`scripts/migrate_data_layout.py` 是旧版扁平数据目录到当前目录结构的一次性、可重复运行迁移工具；当前目录已经迁移完成，通常不需要再次修改或运行。

## 9. 修改后的最低验收标准

### 快速静态检查

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py machining_unified scripts
.\.venv\Scripts\python.exe scripts\check_databases.py
.\.venv\Scripts\python.exe scripts\validate_part_manifest.py
```

### 页面回归

至少确认：

- “工艺推荐”工作区可以打开表单；
- “模型检索”工作区可以切换 STEP、文字、图片三种查询；
- “企业资料问答”工作区可以显示历史对话和输入框；
- 页面启动后没有 Streamlit exception；
- 改动过的检索分支至少执行一次真实查询。

### 数据回归

- `scripts/check_databases.py` 返回退出码 0；
- CAD 目录 `part_id` 唯一；
- CAD 目录和清单引用的源文件存在；
- `cad_semantic`、`multimodal` 数量与 CAD 目录一致；
- 四个 collection 均存在且非空；
- 新索引 metadata 不应再出现 `data/cad_samples` 或 `data/assembly_packages` 旧路径。

## 10. 常见修改应该改哪里

| 需求 | 优先修改位置 |
|---|---|
| 调整页面布局、按钮或提示文字 | `machining_unified/ui/`，必要时修改 `app.py` |
| 调整工业风颜色和样式 | `assets/industrial.css`、`.streamlit/config.toml` |
| 修改工艺推荐链路 | `machining_unified/services/process.py` |
| 修改 STEP 几何特征 | `machining_unified/cad/extraction.py`，随后重建相关索引 |
| 修改几何相似度权重 | `machining_unified/cad/retrieval.py` |
| 修改 CAD 文本 RAG | `machining_unified/retrieval/cad_rag.py` |
| 修改 BM25、类别路由或知识图谱 | `machining_unified/knowledge/engineering.py` |
| 修改图片或 CLIP 检索 | `machining_unified/cad/visual.py`、`machining_unified/retrieval/multimodal.py` |
| 修改企业资料召回和回答约束 | `machining_unified/knowledge/enterprise.py` |
| 修改图纸 OCR/字段提取 | `machining_unified/knowledge/drawing.py` |
| 增加数据路径 | 先改 `machining_unified/config/paths.py` |
| 增加/变更向量库 | 同时改 `paths.py`、`database_registry.py` 和审计脚本 |

## 11. 容易踩坑的地方

- Windows 下 Chroma 的 SQLite 文件可能被 Streamlit、Navicat 或 DataGrip 占用，重建前先关闭相关进程。
- `build_full_knowledge_base.py` 会替换工艺向量库，不要在网页服务运行时执行。
- CAD 目录以 MD5 去重；同内容文件只保留一个可检索主记录，重复来源记录在 `cad_duplicates.json`。
- 文件名只能作为谨慎的类别提示，不能替代 STEP 几何事实。
- OCR 未识别到的尺寸、材料或公差必须保持未知，不能由 LLM 猜测。
- Streamlit widget 状态依赖稳定的 `key`；重命名 key 会影响跨重跑状态和历史交互。
- `st.cache_resource` 缓存向量模型和数据库连接；索引重建后，正在运行的页面可能需要重启才能加载新库。
- 多模态检索是补充召回，不替代严格尺寸、拓扑和加工特征比较。
- `langchain-community` 当前会出现维护状态警告，但现有 `TextLoader/PyPDFLoader` 链路仍可运行；迁移依赖时要做完整文档加载回归。

## 12. 当前已验证状态

最近一次整理完成于 2026-08-13：

- 业务代码已从根目录迁入分层包；
- 旧重复 Python 模块已删除；
- 数据已迁移到当前分区目录；
- 四套向量库已基于新路径重建；
- 数据库 metadata 中旧路径引用数量为 0；
- 数据库完整性检查通过；
- Python 包导入和编译通过；
- 三个 Streamlit 工作区加载通过；
- STEP、文字、CLIP 多模态和企业资料检索回归通过。

开始新任务前，先运行：

```powershell
.\.venv\Scripts\python.exe scripts\check_databases.py
```

完成任务后，在说明中列出修改文件、是否重建数据库、数据库检查结果以及尚未验证的外部依赖。

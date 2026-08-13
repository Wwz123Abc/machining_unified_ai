# 数据目录说明

本目录按数据职责划分，业务代码只通过 `machining_unified/config/paths.py` 访问这些路径。

- `catalogs/`：CAD 检索目录、重复文件清单和跨模态关联清单。
- `knowledge/`：工艺案例、规则、设备刀具资料、来源文档和模板。
- `enterprise/`：企业 STEP、装配包、BOM 和工程图原始资料。
- `vector_stores/`：四套相互独立的 Chroma 向量库。
- `runtime/`：聊天历史等可变运行数据。
- `models/`：OCR 等本地模型资源。

四套向量库不能直接合并，因为它们使用不同的向量模型或评分含义：

- `process/`：工艺案例与工艺资料；
- `cad_semantic/`：CAD 中文工程语义；
- `enterprise/`：STEP、BOM 与工程图证据；
- `multimodal/`：文字、图片与 STEP 的 CLIP 表征。

目录、清单与向量库的只读一致性检查：

```powershell
.\.venv\Scripts\python.exe scripts\check_databases.py
```

索引需要更新时，请先停止网页服务，再按项目根目录 `README.md` 的顺序重建。

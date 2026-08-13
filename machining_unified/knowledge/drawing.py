"""二维图纸输入适配器：PDF 文本层、图片 OCR 可选能力和字段初步抽取。"""

import io
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from machining_unified.config.paths import TESSDATA_DIR


MATERIALS = ("304不锈钢", "6061铝合金", "45钢", "HT250")
PART_TYPES = {
    "箱体类": ("箱体", "阀体", "轴承座"),
    "盘类": ("法兰", "安装板", "盖板", "圆盘"),
    "套筒类": ("套筒", "衬套"),
    # “轴”是宽泛关键词，必须放在箱体/盘/套筒等具体类型之后。
    "轴类": ("传动轴", "阶梯轴", "轴"),
}
SPECIAL_FEATURES = ("薄壁", "键槽", "螺纹", "轴承孔", "内孔", "定位孔", "孔系")


def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    """提取 PDF 的文字层；扫描 PDF 没有文字层时返回空文本。"""

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip(), len(reader.pages)


def extract_image_text(file_bytes: bytes) -> tuple[str, list[str]]:
    """尝试调用可选 pytesseract；没有 OCR 环境时返回可解释警告。"""

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return "", ["当前环境未安装 pytesseract，图片仅完成上传，需人工录入或配置 OCR。"]

    try:
        image = Image.open(io.BytesIO(file_bytes))
        tesseract_candidates = (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        )
        for executable in tesseract_candidates:
            if executable.exists():
                pytesseract.pytesseract.tesseract_cmd = str(executable)
                break
        config = f"--tessdata-dir {TESSDATA_DIR}" if (TESSDATA_DIR / "chi_sim.traineddata").exists() else ""
        language = "chi_sim+eng" if (TESSDATA_DIR / "chi_sim.traineddata").exists() else "eng"
        text = pytesseract.image_to_string(image, lang=language, config=config)
        if not text.strip():
            return "", ["OCR 未提取到文字，请确认图纸清晰度或人工录入关键字段。"]
        return text.strip(), []
    except Exception as error:
        return "", [f"OCR 未执行成功：{error}；请人工确认图纸字段。"]


def extract_drawing_features(text: str) -> dict[str, Any]:
    """从图纸文本中提取可审计的初步字段，未出现的信息保持 None/空数组。"""

    material = next((item for item in MATERIALS if item in text), None)
    part_type = next(
        (name for name, keywords in PART_TYPES.items() if any(keyword in text for keyword in keywords)),
        None,
    )
    dimensions = [float(value) for value in re.findall(r"(?:φ|Φ|直径|外径|内孔|孔径)\s*(\d+(?:\.\d+)?)", text)]
    roughness = sorted(set(re.findall(r"Ra\s*\d+(?:\.\d+)?", text, flags=re.IGNORECASE)))
    precision = sorted(set(re.findall(r"IT\s*\d+", text, flags=re.IGNORECASE)))
    features = [item for item in SPECIAL_FEATURES if item in text]

    return {
        "material": material,
        "part_type": part_type,
        "dimensions_mm": dimensions,
        "roughness_ra": roughness[0] if roughness else None,
        "precision_requirement": precision[0].upper() if precision else None,
        "special_features": features,
    }


def extract_uploaded_drawing(uploaded_file: Any) -> dict[str, Any]:
    """统一处理 Streamlit UploadedFile，返回原文、字段和人工确认状态。"""

    file_name = getattr(uploaded_file, "name", "未命名图纸")
    file_bytes = uploaded_file.getvalue()
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    warnings: list[str] = []
    page_count = None

    if suffix == "pdf":
        text, page_count = extract_pdf_text(file_bytes)
        if not text:
            warnings.append("PDF 没有可提取的文字层，可能是扫描图；请配置 OCR 或人工录入。")
    elif suffix in {"png", "jpg", "jpeg", "webp", "bmp"}:
        text, warnings = extract_image_text(file_bytes)
    else:
        text = ""
        warnings.append("仅支持 PDF、PNG、JPG、JPEG、WEBP、BMP 图纸。")

    features = extract_drawing_features(text)
    missing = [key for key in ("material", "part_type", "roughness_ra") if not features.get(key)]
    if missing:
        warnings.append(f"待人工确认字段：{', '.join(missing)}")

    return {
        "source_file": file_name,
        "page_count": page_count,
        "raw_text": text,
        "features": features,
        "warnings": warnings,
        "needs_review": bool(warnings),
    }


def build_drawing_context(drawing_info: dict[str, Any]) -> str:
    """将图纸抽取结果整理为可放入 RAG 问题的上下文。"""

    if not drawing_info:
        return ""
    feature_text = str(drawing_info["features"])
    raw_text = drawing_info["raw_text"][:12000]
    return (
        f"\n[上传图纸：{drawing_info['source_file']}]\n"
        f"图纸初步字段：{feature_text}\n"
        f"图纸文字层：{raw_text}\n"
        "图纸字段必须经过人工确认；空字段不得猜测。\n"
    )

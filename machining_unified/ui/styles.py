"""加载页面的工业风视觉样式。"""

from base64 import b64encode
from pathlib import Path

import streamlit as st

from machining_unified.config.paths import ASSETS_DIR

# CSS 和背景图在多次 Streamlit 重跑间不变，缓存可以避免重复读盘和 Base64 编码。
@st.cache_data(show_spinner=False)
def load_industrial_css(
    css_path: str,
    css_mtime_ns: int,
    image_path: str,
    image_mtime_ns: int,
) -> str:
    """读取并缓存嵌入背景图后的 CSS；文件更新时自动失效。"""

    # 时间戳只作为缓存键；函数体不需要使用它们。
    del css_mtime_ns, image_mtime_ns
    css_file = Path(css_path)
    image_file = Path(image_path)
    css = css_file.read_text(encoding="utf-8")
    image_data = b64encode(image_file.read_bytes()).decode("ascii")
    # 占位符在 CSS 中只出现一次，避免同一张背景图重复发送给浏览器。
    return css.replace("__BACKGROUND_IMAGE__", image_data)


def apply_industrial_style() -> None:
    """注入独立 CSS，并将本地背景图安全嵌入页面。"""

    css_path = ASSETS_DIR / "industrial.css"
    image_path = ASSETS_DIR / "cnc-workshop-background.webp"

    # 把文件修改时间传入缓存键：修改 CSS/图片后会自动获得新缓存。
    css = load_industrial_css(
        str(css_path),
        css_path.stat().st_mtime_ns,
        str(image_path),
        image_path.stat().st_mtime_ns,
    )

    st.html(f"<style>{css}</style>")

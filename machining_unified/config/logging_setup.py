"""全项目结构化日志配置。

分工约定（与标准库 logging 的推荐用法一致）：

- 库层模块只调用 ``logging.getLogger(__name__)`` 取 logger，**不得**添加 handler
  或设置级别，否则会与宿主应用的配置互相覆盖；
- handler 与格式只在入口处配置一次：``app.py`` 与 ``scripts/*.py`` 各自调用
  :func:`configure_logging`。

不使用 loguru 的原因：Streamlit 自身已经配置了 root logger，loguru 需要额外的
InterceptHandler 才能不产生重复输出；标准库方案零新增依赖，且库层保持中立。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# LogRecord 的固有字段。除此之外的属性都来自调用方通过 extra= 传入的业务上下文，
# 需要原样并入 JSON，否则结构化日志会丢掉最关键的排查信息。
_RESERVED_RECORD_KEYS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)

_configured = False


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON，便于日志采集系统解析。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # 业务上下文：logger.info("...", extra={"part_id": ...}) 传入的字段。
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # ensure_ascii=True：中文被转义为 \uXXXX，输出是纯 ASCII。
        # 这是刻意的取舍——日志要经过 stderr、容器运行时和采集器多段管道，
        # 任何一段用非 UTF-8 编码解码都会把中文变成乱码，实测已复现。
        # 转义后任何 JSON 解析器都能还原出原文，代价只是裸行不便直读。
        # default=str 保证 Path、set 等非 JSON 原生类型不会让日志本身抛异常。
        return json.dumps(payload, ensure_ascii=True, default=str)


def _build_handler(destination: str | None) -> logging.Handler:
    """destination 为空时输出到 stderr，否则追加写入指定文件。"""

    if not destination:
        # 文本格式下中文会直接写入 stderr。Windows 控制台默认不是 UTF-8，
        # 这里尽力把流切到 UTF-8；失败（流已被重定向或不支持）时不阻断，
        # JSON 格式本身已经是 ASCII 安全的。
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
        return logging.StreamHandler(stream=sys.stderr)
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.FileHandler(path, encoding="utf-8")


def configure_logging(force: bool = False) -> logging.Logger:
    """配置项目日志，返回 ``machining_unified`` 这个包级 logger。

    Streamlit 每次重跑都会自上而下重新执行 ``app.py``，因此本函数必须幂等：
    重复调用不得叠加 handler，否则同一条日志会被打印 N 次。

    环境变量：

    ``LOG_LEVEL``   日志级别，默认 ``INFO``；取值非法时回退到 ``INFO``。
    ``LOG_FORMAT``  ``json``（默认）或 ``text``。
    ``LOG_FILE``    指定后写入该文件，否则输出到 stderr。
    """

    global _configured
    logger = logging.getLogger("machining_unified")
    if _configured and not force:
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    try:
        handler = _build_handler(os.getenv("LOG_FILE"))
    except OSError:
        # 日志文件不可写（目录只读、磁盘满、路径非法）不应让应用无法启动，
        # 退回 stderr 并在配置完成后立即记录这次降级。
        handler = logging.StreamHandler(stream=sys.stderr)
        fallback_reason = os.getenv("LOG_FILE")
    else:
        fallback_reason = None

    if os.getenv("LOG_FORMAT", "json").lower() == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())

    logger.addHandler(handler)
    logger.setLevel(level)
    # 不向 root 传播：Streamlit 已在 root 上装了自己的 handler，
    # 传播会让每条日志同时以两种格式各输出一次。
    logger.propagate = False
    _configured = True

    if fallback_reason:
        logger.warning("日志文件不可写，已回退到 stderr", extra={"log_file": fallback_reason})
    return logger


def get_logger(name: str) -> logging.Logger:
    """库层取 logger 的统一入口；不做任何 handler 配置。"""

    return logging.getLogger(name)

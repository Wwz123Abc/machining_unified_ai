"""本地企业知识库对话历史的持久化存储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from threading import RLock
from typing import Any

from machining_unified.config.paths import CHAT_HISTORY_PATH

HISTORY_FILE = CHAT_HISTORY_PATH
_LOCK = RLock()


def new_conversation_id() -> str:
    return f"chat-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _read() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def append_message(conversation_id: str, role: str, content: str, mode: str, source_refs: list[str] | None = None) -> None:
    """原子写入一条对话；仅保存文本、模式和资料引用，不保存密钥。"""
    with _LOCK:
        messages = _read()
        messages.append(
            {
                "conversation_id": conversation_id,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "role": role,
                "content": content,
                "mode": mode,
                "source_refs": source_refs or [],
            }
        )
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = HISTORY_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(HISTORY_FILE)


def list_conversations(limit: int = 30) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for message in _read():
        grouped.setdefault(str(message.get("conversation_id", "")), []).append(message)
    summaries = []
    for conversation_id, messages in grouped.items():
        first_question = next((str(item.get("content", "")) for item in messages if item.get("role") == "user"), "空白对话")
        summaries.append({"id": conversation_id, "label": f"{messages[-1].get('timestamp', '')}｜{first_question[:24]}"})
    return sorted(summaries, key=lambda item: item["id"], reverse=True)[:limit]


def load_conversation(conversation_id: str) -> list[dict[str, Any]]:
    return [message for message in _read() if message.get("conversation_id") == conversation_id]

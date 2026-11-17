"""Bounded model context without altering the stored conversation."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from muschat.reasoning import ChatSession

DEFAULT_HISTORY_MESSAGES = 12


def recent_history(chat: "ChatSession", limit: int = DEFAULT_HISTORY_MESSAGES) -> list:
    if limit < 0:
        raise ValueError("history message limit must not be negative")
    return chat.get_history()[-limit:] if limit else []

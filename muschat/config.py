"""Environment configuration loader with fallback to Streamlit secrets."""

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_EMBED_MODEL = "gemini-embedding-2"
# Suggested installation path in README (gitignored folder on Windows); override via NOTAGEN_PYTHON on other platforms.
DEFAULT_NOTAGEN_PYTHON = ".muschat/notagen/venv/Scripts/python.exe"
DEFAULT_NOTAGEN_DIR = ".muschat/notagen/NotaGen"


class MissingApiKeyError(Exception):
    """Raised when GEMINI_API_KEY is not configured."""


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    embed_model: str
    openmusic_enabled: bool = False
    notagen: tuple[str, str] | None = None  # (NotaGen python executable, repo directory); None = disabled
    history_messages: int = 12
    notagen_worker: bool = False
    notagen_idle_timeout: float = 60


def _read(name: str, env: Mapping, secrets: Mapping) -> str:
    return str(env.get(name) or secrets.get(name) or "").strip()


def load_config(env: Mapping, secrets: Mapping) -> Config:
    api_key = _read("GEMINI_API_KEY", env, secrets)
    if not api_key:
        raise MissingApiKeyError("GEMINI_API_KEY is not configured.")
    history_messages = int(_read("MUSCHAT_HISTORY_MESSAGES", env, secrets) or "12")
    idle_timeout = float(_read("MUSCHAT_NOTAGEN_IDLE_TIMEOUT", env, secrets) or "60")
    if history_messages < 0 or not 0 < idle_timeout < float("inf"):
        raise ValueError("History window must be nonnegative and worker idle timeout must be finite and positive")
    return Config(
        api_key=api_key,
        history_messages=history_messages,
        notagen_worker=_read("MUSCHAT_NOTAGEN_WORKER", env, secrets) == "1",
        notagen_idle_timeout=idle_timeout,
        openmusic_enabled=_read("MUSCHAT_OPENMUSIC", env, secrets) == "1",
        model=_read("GEMINI_MODEL", env, secrets) or DEFAULT_MODEL,
        embed_model=_read("GEMINI_EMBED_MODEL", env, secrets) or DEFAULT_EMBED_MODEL,
        notagen=(
            _read("NOTAGEN_PYTHON", env, secrets) or DEFAULT_NOTAGEN_PYTHON,
            _read("NOTAGEN_DIR", env, secrets) or DEFAULT_NOTAGEN_DIR,
        )
        if _read("MUSCHAT_NOTAGEN", env, secrets) == "1"
        else None,
    )

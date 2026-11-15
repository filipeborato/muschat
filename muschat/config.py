"""Configuração lida do ambiente, com fallback para os secrets do Streamlit."""

from collections.abc import Mapping
from dataclasses import dataclass

MODELO_PADRAO = "gemini-3.1-flash-lite"
MODELO_EMBEDDINGS_PADRAO = "gemini-embedding-2"
# instalação sugerida no README (pasta ignorada pelo git); Windows. Em outro sistema, defina NOTAGEN_PYTHON.
NOTAGEN_PYTHON_PADRAO = ".muschat/notagen/venv/Scripts/python.exe"
NOTAGEN_DIR_PADRAO = ".muschat/notagen/NotaGen"


class ChaveAusente(Exception):
    """GEMINI_API_KEY não foi configurada."""


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    embed_model: str
    openmusic_ativo: bool = False
    notagen: tuple[str, str] | None = None  # (python do ambiente do NotaGen, pasta do repositório); None = desligado


def _ler(nome: str, env: Mapping, secrets: Mapping) -> str:
    return str(env.get(nome) or secrets.get(nome) or "").strip()


def carregar_config(env: Mapping, secrets: Mapping) -> Config:
    api_key = _ler("GEMINI_API_KEY", env, secrets)
    if not api_key:
        raise ChaveAusente("GEMINI_API_KEY não configurada.")
    return Config(
        api_key=api_key,
        openmusic_ativo=_ler("MUSCHAT_OPENMUSIC", env, secrets) == "1",
        model=_ler("GEMINI_MODEL", env, secrets) or MODELO_PADRAO,
        embed_model=_ler("GEMINI_EMBED_MODEL", env, secrets) or MODELO_EMBEDDINGS_PADRAO,
        notagen=(_ler("NOTAGEN_PYTHON", env, secrets) or NOTAGEN_PYTHON_PADRAO,
                 _ler("NOTAGEN_DIR", env, secrets) or NOTAGEN_DIR_PADRAO)
        if _ler("MUSCHAT_NOTAGEN", env, secrets) == "1" else None,
    )

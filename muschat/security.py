"""Sanitization of untrusted text before prompt injection."""

import re

_TAGS = re.compile(
    r"</?\s*(user_question|internal_analysis|excerpts?|current_composition|operation|"
    r"pergunta_usuario|analise_interna|trechos?|composicao_atual|operacao)\b[^>]*>",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    """Strip prompt-delimiter tags from untrusted input to prevent prompt injection."""
    return _TAGS.sub("", text)

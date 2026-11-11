"""Neutralização de texto não confiável antes de ir para um prompt."""

import re

_TAGS = re.compile(r"</?\s*(pergunta_usuario|analise_interna|trechos?|composicao_atual|operacao)\b[^>]*>",
                   re.IGNORECASE)


def neutralizar(texto: str) -> str:
    """Remove nossas tags de delimitação de texto não confiável (prompt injection)."""
    return _TAGS.sub("", texto)

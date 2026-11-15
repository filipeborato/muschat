"""Translation of API and network failures into user-facing messages."""

import httpx2
from google.genai import errors


def error_message(exc: Exception) -> str | None:
    """Return a user-friendly message for known API/network failures, or None for unexpected bugs."""
    if isinstance(exc, errors.ClientError):
        if exc.code == 429:
            return "Limite de requisições da API atingido. Aguarde um minuto e tente de novo."
        if exc.code in (401, 403) or "API_KEY_INVALID" in str(exc.details):
            return "A chave GEMINI_API_KEY foi recusada pela API. Confira se ela está correta."
        return f"A API recusou a requisição ({exc.code}): {exc.message}"
    if isinstance(exc, errors.ServerError):
        return "O serviço do Gemini está instável no momento. Tente de novo em instantes."
    if isinstance(exc, httpx2.TransportError):
        return "Falha de rede ao falar com o Gemini. Verifique sua conexão e tente de novo."
    return None


class RefusalError(Exception):
    """A composition operation cannot safely proceed."""

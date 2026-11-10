"""Tradução de falhas da API/rede em mensagens para o usuário."""

import httpx2
from google.genai import errors


def mensagem_de_erro(exc: Exception) -> str | None:
    """Mensagem amigável para falhas conhecidas; None se for um erro inesperado (bug)."""
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

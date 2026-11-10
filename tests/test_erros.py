import httpx2
import pytest
from google.genai import errors

from muschat.erros import mensagem_de_erro


def _api(cls, code, reason="", msg="falhou"):
    return cls(code, {"error": {"code": code, "message": msg, "details": [{"reason": reason}]}})


def test_429_vira_aviso_de_limite():
    assert "Limite de requisições" in mensagem_de_erro(_api(errors.ClientError, 429))


@pytest.mark.parametrize("code,reason", [(400, "API_KEY_INVALID"), (401, ""), (403, "")])
def test_chave_invalida(code, reason):
    assert "chave" in mensagem_de_erro(_api(errors.ClientError, code, reason)).lower()


def test_outro_4xx_mostra_codigo_e_mensagem_da_api():
    msg = mensagem_de_erro(_api(errors.ClientError, 400, msg="modelo inexistente"))
    assert "400" in msg and "modelo inexistente" in msg


def test_5xx_vira_servico_instavel():
    assert "instável" in mensagem_de_erro(_api(errors.ServerError, 503))


def test_falha_de_rede():
    assert "rede" in mensagem_de_erro(httpx2.ConnectError("sem conexão")).lower()


def test_erro_desconhecido_nao_e_engolido():
    assert mensagem_de_erro(ValueError("bug")) is None

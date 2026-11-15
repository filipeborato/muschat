import pytest

from muschat.config import ChaveAusente, carregar_config


def test_chave_do_env_tem_prioridade_sobre_secrets():
    cfg = carregar_config({"GEMINI_API_KEY": "env-key"}, {"GEMINI_API_KEY": "secret-key"})
    assert cfg.api_key == "env-key"


def test_chave_cai_para_secrets_quando_env_vazio():
    cfg = carregar_config({}, {"GEMINI_API_KEY": "secret-key"})
    assert cfg.api_key == "secret-key"


def test_modelo_padrao_e_sobrescrevivel():
    assert carregar_config({"GEMINI_API_KEY": "k"}, {}).model == "gemini-3.1-flash-lite"
    assert carregar_config({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "outro"}, {}).model == "outro"


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
def test_sem_chave_falha_com_erro_de_dominio(env):
    with pytest.raises(ChaveAusente):
        carregar_config(env, {})


def test_modelo_de_embeddings_padrao_e_sobrescrevivel():
    assert carregar_config({"GEMINI_API_KEY": "k"}, {}).embed_model == "gemini-embedding-2"
    cfg = carregar_config({"GEMINI_API_KEY": "k"}, {"GEMINI_EMBED_MODEL": "gemini-embedding-001"})
    assert cfg.embed_model == "gemini-embedding-001"


def test_notagen_desligado_por_padrao_e_ligado_com_caminhos_padrao_ou_configurados():
    assert carregar_config({"GEMINI_API_KEY": "k"}, {}).notagen is None
    padrao = carregar_config({"GEMINI_API_KEY": "k", "MUSCHAT_NOTAGEN": "1"}, {}).notagen
    assert padrao == (".muschat/notagen/venv/Scripts/python.exe", ".muschat/notagen/NotaGen")
    cfg = carregar_config({"GEMINI_API_KEY": "k"}, {"MUSCHAT_NOTAGEN": "1", "NOTAGEN_PYTHON": "/env/bin/python",
                                                    "NOTAGEN_DIR": "/opt/NotaGen"})
    assert cfg.notagen == ("/env/bin/python", "/opt/NotaGen")

from pathlib import Path

from muschat.config import carregar_config
from muschat.registro import detectar
from muschat.openmusic import InstalacaoOM
from tests.test_motores import _rodar, _plano


def test_config_exige_ativacao_explicita():
    assert not carregar_config({'GEMINI_API_KEY': 'teste', 'OPENMUSIC': 'om.exe'}, {}).openmusic_ativo
    assert carregar_config({'GEMINI_API_KEY': 'teste'}, {'MUSCHAT_OPENMUSIC': '1'}).openmusic_ativo


def test_instalacao_desativada_nao_consulta_externos_nem_interrompe_python():
    def proibido(nome):
        raise AssertionError(nome)
    reg = detectar(lilypond=None, openmusic=InstalacaoOM(Path('om.exe'), '8.0'),
                   openmusic_ativo=False, which=proibido)
    assert reg.capacidade('OpenMusic') is None
    c, r, _ = _rodar(_plano(), registro=reg)
    assert r.versao is not None and c.pendente is None
    _, r, _ = _rodar(_plano(), registro=reg, modo='openmusic')
    assert r.versao is None and 'MUSCHAT_OPENMUSIC=1' in r.resumo

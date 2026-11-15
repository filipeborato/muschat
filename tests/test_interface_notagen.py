from streamlit.testing.v1 import AppTest
from pathlib import Path

from tests.test_notagen import _instalacao


def test_configurar_notagen_na_interface_atualiza_motores_sem_cache_obsoleto(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "teste-local-sem-chamada")
    monkeypatch.setenv("MUSCHAT_NOTAGEN", "0")
    monkeypatch.setenv("MUSCHAT_OPENMUSIC", "0")
    inst = _instalacao(tmp_path)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run(timeout=30)
    assert not app.exception
    app.text_input(key="notagen_python").set_value(inst.python)
    app.text_input(key="notagen_pasta").set_value(inst.pasta)
    app.checkbox(key="notagen_ativo").check().run(timeout=30)
    assert not app.exception
    assert any("NotaGen instalado" in e.value for e in app.success)
    app.radio(key="modo").set_value("notagen").run(timeout=30)
    assert app.session_state["modo"] == "notagen"
    app.text_input(key="notagen_python").set_value(str(tmp_path / "ausente.exe")).run(timeout=30)
    assert any("não encontrado" in e.value for e in app.error)
    app.checkbox(key="notagen_ativo").uncheck().run(timeout=30)
    assert not app.exception and app.session_state["modo"] == "automatico"

from pathlib import Path
from streamlit.testing.v1 import AppTest

from tests.test_notagen import _installation


def test_configuring_notagen_in_ui_updates_engines_without_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-no-call")
    monkeypatch.setenv("MUSCHAT_NOTAGEN", "0")
    monkeypatch.setenv("MUSCHAT_OPENMUSIC", "0")
    inst = _installation(tmp_path)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run(timeout=30)
    assert not app.exception
    app.text_input(key="notagen_python").set_value(inst.python)
    app.text_input(key="notagen_pasta").set_value(inst.directory)
    app.checkbox(key="notagen_ativo").check().run(timeout=30)
    assert not app.exception
    assert any("NotaGen instalado" in e.value or "NotaGen installed" in e.value for e in app.success)
    app.radio(key="modo").set_value("notagen").run(timeout=30)
    assert app.session_state["modo"] == "notagen"
    app.text_input(key="notagen_python").set_value(str(tmp_path / "missing.exe")).run(timeout=30)
    assert any("not found" in e.value or "não encontrado" in e.value for e in app.error)
    app.checkbox(key="notagen_ativo").uncheck().run(timeout=30)
    assert not app.exception and app.session_state["modo"] in ("automatico", "auto")

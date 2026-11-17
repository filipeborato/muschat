"""Session cache behavior through Streamlit's public application test runner."""

from pathlib import Path
from streamlit.testing.v1 import AppTest
from muschat.config import Config
from muschat.reasoning import NullRetriever
from muschat.registry import Registry, NATIVE_PROCEDURES
from tests.test_composition import _quartet


def test_session_detection_and_export_caches_invalidate_explicitly(monkeypatch):
    import muschat.config
    import muschat.registry
    import muschat.rag.search
    import muschat.notation.lilypond

    score = _quartet()
    detected, rendered = [], []
    original = muschat.notation.lilypond.to_lilypond

    def detect(**kwargs):
        detected.append(kwargs)
        return Registry.python_only(NATIVE_PROCEDURES)

    def render(ir, **kwargs):
        rendered.append(kwargs)
        return original(ir, **kwargs)

    monkeypatch.setattr(
        muschat.config, "load_config", lambda *args: Config("test", "test", "test")
    )
    monkeypatch.setattr(muschat.registry, "detect_capabilities", detect)
    monkeypatch.setattr(
        muschat.rag.search, "open_retriever", lambda *args: (NullRetriever(), "")
    )
    monkeypatch.setattr(
        muschat.notation.lilypond, "detect_lilypond", lambda *args: None
    )
    monkeypatch.setattr(muschat.notation.lilypond, "to_lilypond", render)
    app = AppTest.from_file(Path("app.py").resolve(), default_timeout=30).run()
    assert not app.exception
    app.session_state["composition"] = score
    app.run()
    assert not app.exception
    before = len(rendered)
    app.run()
    assert not app.exception
    assert len(detected) == 1 and len(rendered) == before
    app.checkbox(key="transposed-score").check().run()
    assert not app.exception
    assert len(rendered) == before + 1
    next(
        button
        for button in app.sidebar.button
        if button.label == "Redetectar ferramentas"
    ).click().run()
    assert not app.exception
    assert len(detected) == 2 and len(rendered) == before + 2

"""Render local MusicXML -> SVG com Verovio (sem rede). O toolkit só é criado na primeira partitura."""

import re
import threading
from typing import Any

OPCOES = {
    # página larga com quebra automática: ~3-4 compassos por sistema, sem espremer
    "pageWidth": 2400, "pageHeight": 60000, "adjustPageHeight": True, "breaks": "auto",
    "pageMarginLeft": 80, "pageMarginRight": 120, "pageMarginTop": 60, "pageMarginBottom": 60,
    "scale": 45,
    # espaçamento horizontal mais generoso (padrões: linear 0.25, não linear 0.6)
    "spacingLinear": 0.32, "spacingNonLinear": 0.62,
    # mais ar entre pautas/sistemas para textos e dinâmicas não se encostarem
    "spacingStaff": 14, "spacingSystem": 16,
    "header": "none", "footer": "none",  # o título vai na legenda da UI
}

_toolkit: Any = None
_trava = threading.Lock()  # o toolkit do Verovio não é thread-safe e o Streamlit roda sessões em threads
_ABRE_SVG = re.compile(r"(<svg\b[^>]*>)")


def svg_de(musicxml: str) -> str | None:
    """SVG da primeira página, ou None se o Verovio não conseguir ler o documento."""
    global _toolkit
    with _trava:
        if _toolkit is None:
            import verovio  # import tardio: o custo só aparece quando há partitura

            verovio.enableLog(verovio.LOG_OFF)
            _toolkit = verovio.toolkit()
            _toolkit.setOptions(OPCOES)
        try:
            if not _toolkit.loadData(musicxml) or _toolkit.getPageCount() < 1:
                return None
            return _fundo_branco(_toolkit.renderToSVG(1))
        except Exception:  # documento malformado: a UI mostra o MusicXML em vez do desenho
            return None


def _fundo_branco(svg: str) -> str:
    """O SVG do Verovio é transparente: no tema escuro do Streamlit a tinta preta some."""
    return _ABRE_SVG.sub(r'\1<rect width="100%" height="100%" fill="white"/>', svg, count=1)

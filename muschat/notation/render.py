"""Local MusicXML -> SVG rendering with Verovio (offline). The toolkit is instantiated lazily on the first score."""

import re
import threading
from typing import Any

VEROVIO_OPTIONS = {
    # wide page with automatic breaks: ~3-4 measures per system without crowding
    "pageWidth": 2400,
    "pageHeight": 60000,
    "adjustPageHeight": True,
    "breaks": "auto",
    "pageMarginLeft": 80,
    "pageMarginRight": 120,
    "pageMarginTop": 60,
    "pageMarginBottom": 60,
    "scale": 45,
    # generous horizontal spacing (defaults: linear 0.25, non-linear 0.6)
    "spacingLinear": 0.32,
    "spacingNonLinear": 0.62,
    # extra room between staves/systems so text directions and dynamics do not collide
    "spacingStaff": 14,
    "spacingSystem": 16,
    "header": "none",
    "footer": "none",  # title is shown in the UI caption
}

_toolkit: Any = None
_lock = threading.Lock()  # Verovio toolkit is not thread-safe and Streamlit runs sessions in threads
_OPEN_SVG = re.compile(r"(<svg\b[^>]*>)")


def render_svg(musicxml: str) -> str | None:
    """Return first-page SVG string, or None if Verovio cannot parse the document."""
    global _toolkit
    with _lock:
        if _toolkit is None:
            import verovio  # lazy import: cost only paid when rendering a score

            verovio.enableLog(verovio.LOG_OFF)
            _toolkit = verovio.toolkit()
            _toolkit.setOptions(VEROVIO_OPTIONS)
        try:
            if not _toolkit.loadData(musicxml) or _toolkit.getPageCount() < 1:
                return None
            return _white_background(_toolkit.renderToSVG(1))
        except Exception:  # malformed document: UI falls back to displaying raw MusicXML
            return None


def _white_background(svg: str) -> str:
    """Verovio's SVG background is transparent; add a white rect so black ink stays visible in dark themes."""
    return _OPEN_SVG.sub(r'\1<rect width="100%" height="100%" fill="white"/>', svg, count=1)

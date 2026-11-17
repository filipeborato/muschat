"""Optional Csound post-processing, independent of OpenMusic score parsing."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

CSOUND_TIMEOUT = 300
MAX_AUDIO_BYTES = 200_000_000


def find_csound(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Return Csound executable path from CSOUND env var or PATH."""
    return os.environ.get("CSOUND") or which("csound")


def render_csound(
    directory: Path, executable: str | None, *, timeout: float = CSOUND_TIMEOUT
) -> str:
    """Render `muschat.orc` + `muschat.sco` -> `muschat.wav` with Csound; never raises, returns status description."""
    orc, sco, wav = (
        directory / "muschat.orc",
        directory / "muschat.sco",
        directory / "muschat.wav",
    )
    if not (orc.is_file() and sco.is_file()):
        return "audio not rendered: OMChroma did not write muschat.orc/muschat.sco in the task directory"
    if executable is None:
        return "audio not rendered: Csound not found (install it or set CSOUND)"
    try:
        subprocess.run(
            [executable, "-d", "-m0", "-W", "-o", wav.name, orc.name, sco.name],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired:
        return f"audio not rendered: Csound exceeded {timeout:g} s"
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = " ".join(
            str(getattr(exc, "stderr", "") or exc).strip().splitlines()[-2:]
        )
        return f"audio not rendered: Csound failed ({detail})"
    if not wav.is_file() or wav.stat().st_size == 0:
        return "audio not rendered: Csound did not produce muschat.wav"
    if wav.stat().st_size > MAX_AUDIO_BYTES:
        wav.unlink()
        return f"audio discarded: exceeded {MAX_AUDIO_BYTES // 1_000_000} MB"
    return f"audio: muschat.wav in task directory ({directory.name})"


def synthesize_audio(directory: Path) -> dict[str, Any]:
    message = render_csound(directory, find_csound())
    rendered = message.startswith("audio:")
    return {
        "status": "rendered" if rendered else "unavailable",
        "message": message,
        "path": str(directory / "muschat.wav") if rendered else None,
    }

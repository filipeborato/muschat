"""NotaGen-X symbolic engine integration: installation discovery and isolated subprocess execution (ADR 0002)."""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

WEIGHTS_FILE = "weights_notagenx_p_size_16_p_length_1024_p_layers_20_h_size_1280.pth"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "services" / "notagen_generate.py"
TIMEOUT = 600  # seconds


class ComposerBackend(Protocol):
    def generate(self, condition: str, measures: int, seed: int) -> bytes: ...


@dataclass(frozen=True)
class NotaGenInstallation:
    python: str
    directory: str
    conditions: tuple[str, ...]


def detect_notagen(python: str | None, directory: str | None) -> NotaGenInstallation | str:
    """Return `NotaGenInstallation` when Python, weights, and `prompts.txt` are ready, or a reason string otherwise."""
    if not python or not Path(python).is_file():
        return f"NOTAGEN_PYTHON not found: {python}"
    if not directory or not (Path(directory) / "gradio" / WEIGHTS_FILE).is_file():
        return f"NotaGen-X weights not found in {directory}/gradio/{WEIGHTS_FILE}"
    prompts_file = Path(directory) / "gradio" / "prompts.txt"
    if not prompts_file.is_file():
        return f"condition list prompts.txt not found in {prompts_file}"
    conditions = tuple(
        dict.fromkeys(line.strip() for line in prompts_file.read_text(encoding="utf-8").splitlines() if line.strip())
    )
    if not conditions:
        return "prompts.txt is empty"
    return NotaGenInstallation(str(Path(python)), str(Path(directory)), conditions)


class LocalNotaGen:
    """Run `services/notagen_generate.py` in `.venv-notagen` and return raw MusicXML bytes."""

    def __init__(self, installation: NotaGenInstallation, *, timeout: float = TIMEOUT) -> None:
        self.installation = installation
        self.timeout = timeout

    def generate(self, condition: str, measures: int, seed: int) -> bytes:
        if condition not in self.installation.conditions:
            raise ValueError(f"condition {condition!r} is not accepted by NotaGen-X")
        with tempfile.TemporaryDirectory(prefix="muschat-notagen-") as temp_dir:
            output_file = Path(temp_dir) / "output.xml"
            try:
                subprocess.run(
                    [
                        self.installation.python,
                        str(SCRIPT_PATH),
                        self.installation.directory,
                        condition,
                        str(int(measures)),
                        str(int(seed)),
                        str(output_file),
                    ],
                    cwd=str(Path(self.installation.directory) / "gradio"),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"NotaGen exceeded {self.timeout:g} s and was terminated") from exc
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = " ".join(
                    str(getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or exc).strip().splitlines()[-3:]
                )
                raise ValueError(f"NotaGen failed: {detail or exc}") from exc
            if not output_file.is_file() or output_file.stat().st_size == 0:
                raise ValueError("NotaGen did not produce a MusicXML output file")
            return output_file.read_bytes()

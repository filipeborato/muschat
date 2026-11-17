"""NotaGen-X symbolic engine integration: installation discovery and isolated subprocess execution (ADR 0002)."""

import atexit
import json
import math
import os
import queue
import threading
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, IO

WEIGHTS_FILE = "weights_notagenx_p_size_16_p_length_1024_p_layers_20_h_size_1280.pth"
SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "services" / "notagen_generate.py"
)
TIMEOUT = 600  # seconds


class ComposerBackend(Protocol):
    def generate(self, condition: str, measures: int, seed: int) -> bytes: ...


@dataclass(frozen=True)
class NotaGenInstallation:
    python: str
    directory: str
    conditions: tuple[str, ...]


def detect_notagen(
    python: str | None, directory: str | None
) -> NotaGenInstallation | str:
    """Return `NotaGenInstallation` when Python, weights, and `prompts.txt` are ready, or a reason string otherwise."""
    if not python or not Path(python).is_file():
        return f"NOTAGEN_PYTHON not found: {python}"
    if not directory or not (Path(directory) / "gradio" / WEIGHTS_FILE).is_file():
        return f"NotaGen-X weights not found in {directory}/gradio/{WEIGHTS_FILE}"
    prompts_file = Path(directory) / "gradio" / "prompts.txt"
    if not prompts_file.is_file():
        return f"condition list prompts.txt not found in {prompts_file}"
    conditions = tuple(
        dict.fromkeys(
            line.strip()
            for line in prompts_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    )
    if not conditions:
        return "prompts.txt is empty"
    return NotaGenInstallation(
        str(Path(python).resolve()), str(Path(directory).resolve()), conditions
    )


class LocalNotaGen:
    """Run `services/notagen_generate.py` in `.venv-notagen` and return raw MusicXML bytes."""

    def __init__(
        self, installation: NotaGenInstallation, *, timeout: float = TIMEOUT
    ) -> None:
        self.installation = NotaGenInstallation(
            str(Path(installation.python).resolve()),
            str(Path(installation.directory).resolve()),
            installation.conditions,
        )
        self.timeout = timeout

    def generate(self, condition: str, measures: int, seed: int) -> bytes:
        if condition not in self.installation.conditions:
            raise ValueError(f"condition {condition!r} is not accepted by NotaGen-X")
        with tempfile.TemporaryDirectory(prefix="muschat-notagen-") as temp_dir:
            output_file = (Path(temp_dir) / "output.xml").resolve()
            try:
                subprocess.run(
                    [
                        self.installation.python,
                        str(SCRIPT_PATH.resolve()),
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
                raise ValueError(
                    f"NotaGen exceeded {self.timeout:g} s and was terminated"
                ) from exc
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = " ".join(
                    str(getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or exc)
                    .strip()
                    .splitlines()[-3:]
                )
                raise ValueError(f"NotaGen failed: {detail or exc}") from exc
            if not output_file.is_file() or output_file.stat().st_size == 0:
                raise ValueError("NotaGen did not produce a MusicXML output file")
            return output_file.read_bytes()


class PersistentNotaGen(LocalNotaGen):
    """Optional session worker; an inactive or broken worker falls back to per-call generation."""

    def __init__(
        self,
        installation: NotaGenInstallation,
        *,
        idle_timeout: float = 60,
        timeout: float = TIMEOUT,
    ) -> None:
        super().__init__(installation, timeout=timeout)
        if not math.isfinite(idle_timeout) or idle_timeout <= 0:
            raise ValueError("worker idle timeout must be finite and positive")
        self.idle_timeout = idle_timeout
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._stderr: IO[str] | None = None
        self._lock = threading.Lock()
        self._closed = False
        atexit.register(self.close)

    def _start(self) -> None:
        self._stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        process = subprocess.Popen(
            [
                self.installation.python,
                str(SCRIPT_PATH.resolve()),
                "--worker",
                self.installation.directory,
                str(self.idle_timeout),
            ],
            cwd=str(Path(self.installation.directory) / "gradio"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            start_new_session=os.name != "nt",
        )
        self._process = process
        if self._closed:
            self._stop()
            raise EOFError("NotaGen worker has been closed")
        responses: queue.Queue[str | None] = queue.Queue()
        self._responses = responses

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                responses.put(line)
            responses.put(None)

        threading.Thread(target=read, daemon=True).start()

    def generate(self, condition: str, measures: int, seed: int) -> bytes:
        if condition not in self.installation.conditions:
            raise ValueError(f"condition {condition!r} is not accepted by NotaGen-X")
        with self._lock:
            if self._closed:
                raise ValueError("NotaGen worker has been closed")
            if self._process is not None and self._process.poll() is not None:
                self._stop()
                return super().generate(condition, measures, seed)
            try:
                if self._process is None:
                    self._start()
                process = self._process
                if process is None or process.stdin is None:
                    raise EOFError("NotaGen worker is unavailable")
                with tempfile.TemporaryDirectory(
                    prefix="muschat-notagen-worker-"
                ) as directory:
                    output = Path(directory) / "output.xml"
                    process.stdin.write(
                        json.dumps(
                            {
                                "condition": condition,
                                "measures": measures,
                                "seed": seed,
                                "output": str(output.resolve()),
                            }
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                    try:
                        line = self._responses.get(timeout=self.timeout)
                    except queue.Empty as exc:
                        self._stop()
                        raise ValueError(
                            f"NotaGen worker exceeded {self.timeout:g} s and was terminated"
                        ) from exc
                    if line is None:
                        raise EOFError("NotaGen worker exited")
                    response = json.loads(line)
                    if not isinstance(response, dict) or not isinstance(
                        response.get("ok"), bool
                    ):
                        raise EOFError("Invalid NotaGen worker response")
                    if not response["ok"]:
                        raise ValueError(
                            f"NotaGen failed: {response.get('error', 'worker error')}"
                        )
                    if not output.is_file() or output.stat().st_size == 0:
                        raise ValueError(
                            "NotaGen worker did not produce a MusicXML output file"
                        )
                    return output.read_bytes()
            except (OSError, EOFError, json.JSONDecodeError) as exc:
                if self._closed:
                    raise ValueError("NotaGen worker has been closed") from exc
                self._stop()
                return super().generate(condition, measures, seed)

    def close(self) -> None:
        self._closed = True
        self._stop()

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                    )
                else:
                    process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

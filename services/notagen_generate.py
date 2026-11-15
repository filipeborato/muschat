"""Isolated NotaGen-X runner: executed in the `.venv-notagen` interpreter (ADR 0002) and writes MusicXML to disk."""

from contextlib import redirect_stdout
import math
import json
import queue
import threading
from collections.abc import Callable
import os
import random
import re
import sys
from pathlib import Path
from types import ModuleType


class _MeasureLimitReached(Exception):
    pass


class _MeasureOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.lines: list[str] = []
        self.line = ""
        self.skip_diagnostic_newline = False

    def write(self, text: str) -> int:
        if text == "Stream generating...":
            self.skip_diagnostic_newline = True
            return len(text)
        if self.skip_diagnostic_newline:
            self.skip_diagnostic_newline = False
            if text == "\n":
                return len(text)
        for char in text:
            self.line += char
            if char == "/" and (match := re.match(r"^\[r:(\d+)/", self.line)):
                if int(match[1]) >= self.limit:
                    raise _MeasureLimitReached
            if char == "\n":
                self.lines.append(self.line)
                self.line = ""
        return len(text)

    def flush(self) -> None:
        pass


def generate_measures(
    inference: ModuleType,
    period: str,
    composer: str,
    instrumentation: str,
    measures: int,
) -> str:
    """Stop at the next measure marker, retaining complete synchronized rows for all voices."""
    if not 1 <= measures <= 16:
        raise ValueError("NotaGen measure count must be from 1 to 16")
    output = _MeasureOutput(measures)
    # shortcut: upstream has no stop callback; replace this adapter when it exposes a native measure limit.
    try:
        with redirect_stdout(output):
            abc = inference.inference_patch(period, composer, instrumentation)
    except _MeasureLimitReached:
        lines = inference.rest_unreduce(output.lines)
        abc = "X:1\n" + "".join(
            line
            for line in lines
            if not (line.startswith("%") and not line.startswith("%%"))
        )
    return to_voice_blocks(abc)


def to_voice_blocks(abc: str) -> str:
    """Express synchronized inline voices as separate voice blocks for music21's ABC reader."""
    header: list[str] = []
    declarations: dict[str, str] = {}
    voices: dict[str, list[str]] = {}
    for line in abc.splitlines():
        if line.startswith("V:"):
            declarations[line.split()[0][2:]] = line
        elif "[V:" in line:
            for voice, music in re.findall(r"\[V:([^\]]+)\](.*?)(?=\[V:|$)", line):
                voices.setdefault(voice, []).append(music)
        else:
            header.append(line)
    if not voices:
        return abc
    for voice, fragments in voices.items():
        header.extend([declarations.get(voice, f"V:{voice}"), "".join(fragments)])
    return "\n".join(header) + "\n"


def load_runtime(notagen_dir: str):
    """Load native libraries and model on the main thread before starting IPC readers."""
    root = Path(notagen_dir).resolve()
    gradio_dir = root / "gradio"
    os.chdir(gradio_dir)
    for folder in (root, gradio_dir):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    with redirect_stdout(sys.stderr):
        import numpy as np
        import torch
        import inference
        import music21
    return np, torch, inference, music21


def render_request(
    notagen_dir: str, condition: str, measures: int, seed: int, output_path: str
) -> None:
    output_path = str(Path(output_path).resolve())
    np, torch, inference, music21 = load_runtime(notagen_dir)

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    period, composer, instrumentation = condition.split("_", 2)
    abc_output = generate_measures(
        inference, period, composer, instrumentation, measures
    )
    score = music21.converter.parse(abc_output, format="abc")
    score.write("musicxml", fp=output_path)


def serve_requests(render: Callable[[dict], None], idle_timeout: float) -> None:
    """Read one JSON request per line; release model memory by exiting after idle timeout."""
    if not math.isfinite(idle_timeout) or idle_timeout <= 0:
        raise ValueError("worker idle timeout must be finite and positive")
    requests: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in sys.stdin:
            requests.put(line)
        requests.put(None)

    threading.Thread(target=read, daemon=True).start()
    while True:
        try:
            line = requests.get(timeout=idle_timeout)
        except queue.Empty:
            return
        if line is None:
            return
        try:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                render(request)
            response: dict[str, object] = {"ok": True}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        print(json.dumps(response), flush=True)


def main() -> None:
    if sys.argv[1] == "--worker":
        directory, idle = sys.argv[2:4]
        directory = str(Path(directory).resolve())
        load_runtime(directory)
        serve_requests(
            lambda r: render_request(
                directory,
                r["condition"],
                int(r["measures"]),
                int(r["seed"]),
                r["output"],
            ),
            float(idle),
        )
    else:
        directory, condition, measures, seed, output = sys.argv[1:6]
        render_request(directory, condition, int(measures), int(seed), output)


if __name__ == "__main__":
    main()

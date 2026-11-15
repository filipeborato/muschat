"""Isolated NotaGen-X runner: executed in the `.venv-notagen` interpreter (ADR 0002) and writes MusicXML to disk."""

from contextlib import redirect_stdout
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


def main() -> None:
    notagen_dir, condition, measures_str, seed_str, output_path = sys.argv[1:6]
    measures, seed = int(measures_str), int(seed_str)
    notagen_dir = str(Path(notagen_dir).resolve())
    output_path = str(Path(output_path).resolve())
    gradio_dir = Path(notagen_dir) / "gradio"
    os.chdir(gradio_dir)
    sys.path.insert(0, str(gradio_dir))
    sys.path.insert(0, str(Path(notagen_dir)))

    import numpy as np
    import torch
    import inference
    import music21

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


if __name__ == "__main__":
    main()

"""Isolated NotaGen-X runner: executed in the `.venv-notagen` interpreter (ADR 0002) and writes MusicXML to disk."""

import os
import random
import re
import sys
from pathlib import Path


def _trim_abc_measures(abc_text: str, max_measures: int) -> str:
    """Trim each voice of an ABC score to at most `max_measures` bars while keeping header lines intact."""
    header, body = [], []
    for line in abc_text.splitlines():
        if re.match(r"^[A-Za-z]:", line) and not line.startswith("V:"):
            header.append(line)
        else:
            body.append(line)
    voices: list[list[str]] = []
    for line in body:
        if line.startswith("V:") or not voices:
            voices.append([line])
        else:
            voices[-1].append(line)
    trimmed_voices = []
    for voice_lines in voices:
        v_head = [l for l in voice_lines if l.startswith("V:") or l.startswith("%%")]
        music = " ".join(l for l in voice_lines if not l.startswith("V:") and not l.startswith("%%"))
        bars = [b for b in music.split("|") if b.strip()]
        kept = "|".join(bars[:max_measures]) + "|]"
        trimmed_voices.extend([*v_head, kept])
    return "\n".join([*header, *trimmed_voices]) + "\n"


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
    from inference import inference_patch
    import music21

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    period, composer, instrumentation = condition.split("_", 2)
    abc_output = inference_patch(period, composer, instrumentation)
    trimmed = _trim_abc_measures(abc_output, measures)
    score = music21.converter.parse(trimmed, format="abc")
    score.write("musicxml", fp=output_path)


if __name__ == "__main__":
    main()

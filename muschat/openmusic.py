"""OpenMusic assisted bridge (ADR 0001): Muschat prepares an `OmTask` (numeric-only input, approved patch, and
instructions), the composer runs the patch in OpenMusic and returns `saida.txt`, which is validated into `ScoreIR`.

No Lisp code is generated or executed at runtime.
"""

import io
import os
import random
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from muschat.composition import OmTask
from muschat.om_patches import PATCH_FAILED, PATCHES, ApprovedPatch, PatchContext
from muschat.plan import Plan, normalize_parameter_name, normalize_procedure_id
from muschat.score_ir import Provenance, ScoreIR

# shortcut: relative to the working directory (project root); make configurable if launched elsewhere
TASKS_DIR = Path(".muschat") / "openmusic"
MAX_OUTPUT_BYTES = 256_000
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class OmInstallation:
    executable: Path
    version: str | None = None
    libraries: dict[str, Path] = field(default_factory=dict)


def _default_roots() -> list[Path]:
    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        str(Path.home()),
    ]
    return [
        d
        for b in filter(None, bases)
        if Path(b).is_dir()
        for d in Path(b).glob("OpenMusic*")
        if d.is_dir()
    ]


def _find_executable(path: Path) -> Path | None:
    if path.is_file():
        return path
    if path.is_dir():
        return next(
            (
                p
                for pattern in ("OpenMusic*.exe", "OM*.exe", "OpenMusic*.app", "OpenMusic*")
                for p in sorted(path.glob(pattern))
                if p != path and (p.is_file() or p.suffix == ".app")
            ),
            None,
        )
    return None


def _scan_libraries(*folders: Path) -> dict[str, Path]:
    """Discover installed OpenMusic library folders normalized by versionless lowercase name."""
    found: dict[str, Path] = {}
    for folder in folders:
        if folder.is_dir():
            for d in sorted(folder.iterdir()):
                if d.is_dir():
                    name = re.sub(r"[\s_-]*v?\d[\d.]*$", "", d.name).strip().lower()
                    found.setdefault(name, d)
    return found


def detect_openmusic(
    configured: str | None = None,
    *,
    roots: list[Path] | None = None,
    user_libraries_dir: Path | None = None,
) -> OmInstallation | None:
    """Detect OpenMusic in OPENMUSIC or standard installation directories and scan installed libraries."""
    candidates = ([Path(configured)] if configured else []) + (
        roots if roots is not None else _default_roots()
    )
    user_lib = user_libraries_dir if user_libraries_dir is not None else Path.home() / "OM" / "Libraries"
    for root in candidates:
        exe = _find_executable(root)
        if exe is None:
            continue
        folder = exe.parent if exe.is_file() else exe
        m = re.search(r"(\d+(?:\.\d+)+)", f"{folder.name} {exe.name}")
        return OmInstallation(
            exe,
            m[1] if m else None,
            _scan_libraries(user_lib, folder / "libraries", folder / "Libraries"),
        )
    return None


def task_input(task: OmTask) -> str:
    patch = PATCHES[task.procedure]
    return task.content if task.content is not None else patch.build_input(task.parameters, None, None)


def task_instructions(task: OmTask) -> str:
    p = PATCHES[task.procedure]
    steps = "\n".join(f"{n}. {s}" for n, s in enumerate(p.steps, 1))
    file_note = (
        f"Approved patch: `{p.file}` (copied into this folder)."
        if p.file
        else "This patch has not yet been validated in real OpenMusic and is not included in the repository."
    )
    lines = task_input(task).strip().splitlines()
    preview = "\n".join(lines[:8]) + (f"\n… ({len(lines)} lines)" if len(lines) > 8 else "")
    return (
        f"# OpenMusic Task {task.id}: {p.om_function} ({p.library})\n\n{p.description}\n\n{file_note}\n\n"
        f"Input (`entrada.txt`): {p.input_format}\n\n```\n{preview}\n```\n\n"
        f"Expected output: {p.output_format}.\n\n{steps}\n"
    )


def task_package(task: OmTask) -> bytes:
    """Build a ZIP archive with `entrada.txt`, `instrucoes.md`, and the approved patch file."""
    p = PATCHES[task.procedure]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("entrada.txt", task_input(task))
        z.writestr("instrucoes.md", task_instructions(task))
        if p.file and Path(p.file).is_file():
            z.write(p.file, Path(p.file).name)
    return buffer.getvalue()


# Attach helper methods to OmTask so task.input_text(), task.instructions(), task.package() work directly
OmTask.patch = property(lambda self: PATCHES[self.procedure])  # type: ignore[attr-defined]
OmTask.input_text = task_input  # type: ignore[attr-defined]
OmTask.instructions = task_instructions  # type: ignore[attr-defined]
OmTask.package = task_package  # type: ignore[attr-defined]


def create_task(
    procedure: str,
    parameters: dict[str, float],
    *,
    root: Path = TASKS_DIR,
    current: ScoreIR | None = None,
    plan: Plan | None = None,
) -> OmTask:
    """Build numeric `entrada.txt` and write the task folder before pausing for the composer."""
    proc = normalize_procedure_id(procedure)
    norm_params = {normalize_parameter_name(k, proc): float(v) for k, v in parameters.items()}
    if proc not in PATCHES or not PATCHES[proc].file:
        raise ValueError(f"no approved patch for {procedure}")
    content = PATCHES[proc].build_input(norm_params, current, plan)
    task_id = f"om-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    folder = root / task_id
    task = OmTask(id=task_id, procedure=proc, parameters=norm_params, directory=str(folder), content=content)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "entrada.txt").write_text(task_input(task), encoding="utf-8")
    (folder / "instrucoes.md").write_text(task_instructions(task), encoding="utf-8")
    patch_file = PATCHES[proc].file
    if patch_file and Path(patch_file).is_file():
        shutil.copyfile(patch_file, folder / Path(patch_file).name)
    return task


def import_output(
    task: OmTask,
    output: bytes | str,
    *,
    operation: str,
    current: ScoreIR | None = None,
    plan: Plan | None = None,
    rng: random.Random | None = None,
) -> ScoreIR:
    """Validate `saida.txt` (size, UTF-8 text, numbers only) and convert into `ScoreIR` with `openmusic` provenance."""
    raw = output if isinstance(output, bytes) else output.encode("utf-8")
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError(f"output has {len(raw)} bytes; limit is {MAX_OUTPUT_BYTES}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("output is not valid UTF-8 text") from exc
    tokens = [t for t in re.split(r"[\s()]+", text) if t]
    if invalid := [t for t in tokens if not _NUMBER_RE.match(t)]:
        raise ValueError(f"output contains non-numeric tokens: {invalid[:3]}")
    values = [float(x) for x in tokens]
    folder = Path(task.directory)
    if values == [PATCH_FAILED]:
        err_file = folder / "erro.txt"
        detail = err_file.read_text(encoding="utf-8", errors="replace")[:500] if err_file.is_file() else "no erro.txt"
        raise ValueError(f"patch failed in OpenMusic: {detail.strip()}")
    p = PATCHES[task.procedure]
    ctx = PatchContext(
        parameters=task.parameters,
        input_text=task_input(task),
        directory=folder,
        current=current,
        plan=plan,
        rng=rng or random.Random(0),
        provenance=Provenance(source="openmusic", author=p.author, operation=operation),
    )
    return p.read_output(values, ctx)

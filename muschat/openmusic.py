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

from pydantic import BaseModel, Field, model_validator

from muschat.composition import OmTask
from muschat.om_patches import PATCH_FAILED, PATCHES as _CANONICAL_PATCHES, ApprovedPatch, PatchContext
from muschat.plan import Plan, normalize_parameter_name, normalize_procedure_id
from muschat.score_ir import Provenance, ScoreIR

# shortcut: relative to the working directory (project root); make configurable if launched elsewhere
TASKS_DIR = Path(".muschat") / "openmusic"
MAX_OUTPUT_BYTES = 256_000
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


# shortcut: dual-key registry shim for unmigrated callers; remove in Ticket 03 when patches_om.py is deleted
class _PatchRegistry(dict[str, ApprovedPatch]):
    def __getitem__(self, key: str) -> Any:
        if key in _CANONICAL_PATCHES:
            return _CANONICAL_PATCHES[key]
        if super().__contains__(key):
            return super().__getitem__(key)
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES

        if key in _LEGACY_PATCHES:
            return _LEGACY_PATCHES[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, ApprovedPatch):
            _CANONICAL_PATCHES[key] = value
            super().__setitem__(key, value)
        else:
            from muschat.patches_om import PATCHES as _LEGACY_PATCHES

            _LEGACY_PATCHES[key] = value
            proc = normalize_procedure_id(key)
            if proc in _CANONICAL_PATCHES:
                p = _CANONICAL_PATCHES[proc]
                updated = ApprovedPatch(
                    procedure=p.procedure,
                    library=p.library,
                    om_function=p.om_function,
                    description=p.description,
                    input_format=p.input_format,
                    output_format=p.output_format,
                    steps=p.steps,
                    build_input=p.build_input,
                    read_output=p.read_output,
                    author=p.author,
                    file=getattr(value, "arquivo", p.file),
                    validated=getattr(value, "validado", p.validated),
                    executables=p.executables,
                )
                _CANONICAL_PATCHES[proc] = updated
                super().__setitem__(proc, updated)

    def __contains__(self, key: object) -> bool:
        if key in _CANONICAL_PATCHES or super().__contains__(key):
            return True
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES

        return key in _LEGACY_PATCHES

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def items(self) -> Any:
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES

        return _LEGACY_PATCHES.items()

    def values(self) -> Any:
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES

        return _LEGACY_PATCHES.values()


PATCHES: dict[str, ApprovedPatch] = _PatchRegistry(_CANONICAL_PATCHES)


@dataclass(frozen=True)
class OmInstallation:
    executable: Path
    version: str | None = None
    libraries: dict[str, Path] = field(default_factory=dict)

    @property
    def executavel(self) -> Path:
        return self.executable

    @property
    def versao(self) -> str | None:
        return self.version

    @property
    def bibliotecas(self) -> dict[str, Path]:
        return self.libraries


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
    raizes: list[Path] | None = None,
    pasta_usuario: Path | None = None,
) -> OmInstallation | None:
    """Detect OpenMusic in OPENMUSIC or standard installation directories and scan installed libraries."""
    search_roots = roots if roots is not None else raizes
    user_dir = user_libraries_dir if user_libraries_dir is not None else pasta_usuario
    candidates = ([Path(configured)] if configured else []) + (
        search_roots if search_roots is not None else _default_roots()
    )
    user_lib = user_dir if user_dir is not None else Path.home() / "OM" / "Libraries"
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
OmTask.entrada = task_input  # type: ignore[attr-defined]
OmTask.instrucoes = task_instructions  # type: ignore[attr-defined]
OmTask.pacote = task_package  # type: ignore[attr-defined]
OmTask.pasta = property(lambda self: self.directory)  # type: ignore[attr-defined]
OmTask.procedimento = property(lambda self: self.procedure)  # type: ignore[attr-defined]
OmTask.parametros = property(lambda self: self.parameters)  # type: ignore[attr-defined]


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
    p = _CANONICAL_PATCHES[task.procedure]
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


# shortcut: temporary legacy compatibility shims for unmigrated callers; remove in Ticket 03
PASTA_TAREFAS = TASKS_DIR
LIMITE_SAIDA = MAX_OUTPUT_BYTES
InstalacaoOM = OmInstallation
PatchAprovado = ApprovedPatch
detectar_openmusic = detect_openmusic


class Tarefa(BaseModel):
    """Legacy Portuguese Tarefa schema shim for unmigrated callers (removed in Ticket 03)."""

    id: str
    procedimento: str
    parametros: dict[str, float]
    pasta: str
    criada: datetime = Field(default_factory=datetime.now)
    conteudo: str | None = None

    @model_validator(mode="after")
    def _no_catalogo(self) -> "Tarefa":
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES
        from muschat.procedimentos import CATALOGO as _LEGACY_CATALOGO

        if self.procedimento not in _LEGACY_PATCHES or self.procedimento not in _LEGACY_CATALOGO:
            raise ValueError(f"tarefa aponta para procedimento sem patch: {self.procedimento}")
        faixas = _LEGACY_CATALOGO[self.procedimento].parametros
        if set(self.parametros) != set(faixas):
            raise ValueError(f"parâmetros da tarefa não batem com {self.procedimento}")
        for nome, val in self.parametros.items():
            f = faixas[nome]
            if not f.minimo <= val <= f.maximo or (f.inteiro and val != int(val)):
                raise ValueError(f"parâmetro {nome}={val:g} fora da faixa na tarefa")
        return self

    @property
    def patch(self) -> Any:
        from muschat.patches_om import PATCHES as _LEGACY_PATCHES

        return _LEGACY_PATCHES[self.procedimento]

    def entrada(self) -> str:
        return self.conteudo if self.conteudo is not None else self.patch.montar(self.parametros, None, None)

    def instrucoes(self) -> str:
        p = self.patch
        passos = "\n".join(f"{n}. {s}" for n, s in enumerate(p.passos, 1))
        nota_arquivo = (
            f"Patch aprovado: `{p.arquivo}` (copiado para esta pasta)."
            if p.arquivo
            else "Este patch ainda não foi validado no OpenMusic real e não está no projeto."
        )
        linhas = self.entrada().strip().splitlines()
        amostra = "\n".join(linhas[:8]) + (f"\n… ({len(linhas)} linhas)" if len(linhas) > 8 else "")
        return (
            f"# Tarefa OpenMusic {self.id}: {p.funcao_om} ({p.biblioteca})\n\n{p.descricao}\n\n{nota_arquivo}\n\n"
            f"Entrada (`entrada.txt`): {p.entrada}\n\n```\n{amostra}\n```\n\nSaída esperada: {p.saida}.\n\n{passos}\n"
        )

    def pacote(self) -> bytes:
        p = self.patch
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("entrada.txt", self.entrada())
            z.writestr("instrucoes.md", self.instrucoes())
            if p.arquivo and Path(p.arquivo).is_file():
                z.write(p.arquivo, Path(p.arquivo).name)
        return buf.getvalue()


def _legacy_criar_tarefa(
    procedimento: str,
    parametros: dict[str, float],
    *,
    raiz: Path = TASKS_DIR,
    atual: Any = None,
    plano: Any = None,
) -> Tarefa:
    from muschat.patches_om import PATCHES as _LEGACY_PATCHES

    if procedimento not in _LEGACY_PATCHES or not _LEGACY_PATCHES[procedimento].arquivo:
        raise ValueError(f"sem patch aprovado para {procedimento}")
    conteudo = _LEGACY_PATCHES[procedimento].montar(parametros, atual, plano)
    tid = f"om-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    pasta = raiz / tid
    t = Tarefa(
        id=tid,
        procedimento=procedimento,
        parametros={k: float(v) for k, v in parametros.items()},
        pasta=str(pasta),
        conteudo=conteudo,
    )
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "entrada.txt").write_text(t.entrada(), encoding="utf-8")
    (pasta / "instrucoes.md").write_text(t.instrucoes(), encoding="utf-8")
    arquivo_patch = _LEGACY_PATCHES[procedimento].arquivo
    if arquivo_patch and Path(arquivo_patch).is_file():
        shutil.copyfile(arquivo_patch, pasta / Path(arquivo_patch).name)
    return t


def _legacy_importar(
    tarefa: Tarefa,
    saida: bytes | str,
    *,
    operacao: str,
    atual: Any = None,
    plano: Any = None,
    rng: random.Random | None = None,
) -> Any:
    from muschat import musicir as legacy_ir
    from muschat.patches_om import FALHOU as LEGACY_FALHOU, PATCHES as LEGACY_PATCHES, Contexto

    bruto = saida if isinstance(saida, bytes) else saida.encode("utf-8")
    if len(bruto) > MAX_OUTPUT_BYTES:
        raise ValueError(f"a saída tem {len(bruto)} bytes; o limite é {MAX_OUTPUT_BYTES}")
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("a saída não é texto (UTF-8)") from exc
    partes = [t for t in re.split(r"[\s()]+", texto) if t]
    if invalidas := [t for t in partes if not _NUMBER_RE.match(t)]:
        raise ValueError(f"a saída tem algo além de números separados por espaço: {invalidas[:3]}")
    valores = [float(x) for x in partes]
    pasta = Path(tarefa.pasta)
    if valores == [LEGACY_FALHOU]:
        arq = pasta / "erro.txt"
        detalhe = arq.read_text(encoding="utf-8", errors="replace")[:500] if arq.is_file() else "sem erro.txt"
        raise ValueError(f"o patch falhou no OpenMusic: {detalhe.strip()}")
    p = LEGACY_PATCHES[tarefa.procedimento]
    ctx = Contexto(
        parametros=tarefa.parametros,
        entrada=tarefa.entrada(),
        pasta=pasta,
        atual=atual,
        plano=plano,
        rng=rng or random.Random(0),
        proveniencia=legacy_ir.Proveniencia(origem="openmusic", autor=p.autor, operacao=operacao),
    )
    return p.ler(valores, ctx)


criar_tarefa = _legacy_criar_tarefa
importar = _legacy_importar

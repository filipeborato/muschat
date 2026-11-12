"""Bridge OpenMusic, estratégia assistida (ADR 0001): o Muschat prepara a Tarefa (entrada só com números, patch
aprovado, instruções), o compositor roda o patch no OM e devolve o arquivo de saída, que é validado e vira MusicIR.

Nenhum Lisp é gerado nem executado; nada do OM roda a partir daqui.
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

from pydantic import BaseModel, Field, model_validator

from muschat import musicir as ir
from muschat.patches_om import FALHOU, PATCHES, Contexto, PatchAprovado, material
from muschat.plano import Plano

# shortcut: relativa à pasta onde o app roda (a raiz do projeto); evoluir para uma pasta configurável se o app
# passar a rodar de outro lugar
PASTA_TAREFAS = Path(".muschat") / "openmusic"  # ignorada pelo git
LIMITE_SAIDA = 256_000  # bytes: uma análise espectral de 64 quadros x 64 parciais cabe com folga
_NUMERO = re.compile(r"^-?\d+(?:\.\d+)?$")  # um valor; separadores aceitos: espaços e parênteses (lista Lisp)


# --- descoberta da instalação ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InstalacaoOM:
    executavel: Path
    versao: str | None
    bibliotecas: dict[str, Path] = field(default_factory=dict)  # nome normalizado ("esquisse") -> pasta


# shortcut: só as pastas de instalação do Windows; macOS/Linux entram pela variável OPENMUSIC
def _raizes_padrao() -> list[Path]:
    bases = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
             os.path.expandvars(r"%LOCALAPPDATA%\Programs"), str(Path.home())]
    return [d for b in filter(None, bases) if Path(b).is_dir()
            for d in Path(b).glob("OpenMusic*") if d.is_dir()]


def _executavel(caminho: Path) -> Path | None:
    if caminho.is_file():
        return caminho
    if caminho.is_dir():
        return next((p for padrao in ("OpenMusic*.exe", "OM*.exe", "OpenMusic*.app", "OpenMusic*")
                     for p in sorted(caminho.glob(padrao)) if p != caminho and (p.is_file() or p.suffix == ".app")),
                    None)
    return None


def _bibliotecas(*pastas: Path) -> dict[str, Path]:
    """Subpastas de bibliotecas, pelo nome sem versão: "Esquisse 2.1" -> "esquisse"."""
    achadas: dict[str, Path] = {}
    for pasta in pastas:
        if pasta.is_dir():
            for d in sorted(pasta.iterdir()):
                if d.is_dir():
                    nome = re.sub(r"[\s_-]*v?\d[\d.]*$", "", d.name).strip().lower()
                    achadas.setdefault(nome, d)
    return achadas


def detectar_openmusic(configurado: str | None = None, *, raizes: list[Path] | None = None,
                       pasta_usuario: Path | None = None) -> InstalacaoOM | None:
    """OpenMusic em OPENMUSIC (executável ou pasta) ou nas pastas padrão do Windows; bibliotecas em ~/OM/Libraries
    (OM 6.13+) e na pasta `libraries` da instalação."""
    candidatos = ([Path(configurado)] if configurado else []) + (raizes if raizes is not None else _raizes_padrao())
    usuario = pasta_usuario if pasta_usuario is not None else Path.home() / "OM" / "Libraries"
    for raiz in candidatos:
        exe = _executavel(raiz)
        if exe is None:
            continue
        pasta = exe.parent if exe.is_file() else exe
        m = re.search(r"(\d+(?:\.\d+)+)", f"{pasta.name} {exe.name}")
        return InstalacaoOM(exe, m[1] if m else None, _bibliotecas(usuario, pasta / "libraries", pasta / "Libraries"))
    return None


# --- patches aprovados: formatos em muschat.patches_om ---------------------------------------------------------------

# --- Tarefa ---------------------------------------------------------------------------------------------------------------

class Tarefa(BaseModel):
    """Pacote de uma etapa assistida: guardado na Composição (sobrevive a rerun e vai no JSON)."""

    id: str
    procedimento: str
    parametros: dict[str, float]
    pasta: str
    criada: datetime = Field(default_factory=datetime.now)
    conteudo: str | None = None  # entrada.txt enviada; None nos JSONs da série harmônica anteriores ao campo

    @model_validator(mode="after")
    def _valida(self) -> "Tarefa":
        """A Tarefa pode voltar de um JSON importado: procedimento e parâmetros passam de novo pelas faixas."""
        from muschat.procedimentos import CATALOGO  # tardio: procedimentos não depende do bridge

        if self.procedimento not in PATCHES or self.procedimento not in CATALOGO:
            raise ValueError(f"tarefa com procedimento sem patch aprovado: {self.procedimento}")
        faixas = CATALOGO[self.procedimento].parametros
        if set(self.parametros) != set(faixas):
            raise ValueError(f"parâmetros da tarefa não batem com {self.procedimento}")
        for nome, valor in self.parametros.items():
            f = faixas[nome]
            if not f.minimo <= valor <= f.maximo or (f.inteiro and valor != int(valor)):
                raise ValueError(f"tarefa: {nome}={valor:g} fora da faixa")
        return self

    @property
    def patch(self) -> PatchAprovado:
        return PATCHES[self.procedimento]

    def entrada(self) -> str:
        # sem `conteudo` só existem JSONs antigos da série harmônica, cuja entrada vem só dos parâmetros
        return self.conteudo if self.conteudo is not None else self.patch.montar(self.parametros, None, None)

    def instrucoes(self) -> str:
        p = self.patch
        passos = "\n".join(f"{n}. {s}" for n, s in enumerate(p.passos, 1))
        arquivo = (f"Patch aprovado: `{p.arquivo}` (copiado nesta pasta)." if p.arquivo else
                   "Este patch ainda não foi validado no OpenMusic real e não está no projeto.")
        linhas = self.entrada().strip().splitlines()
        conteudo = "\n".join(linhas[:8]) + (f"\n… ({len(linhas)} linhas)" if len(linhas) > 8 else "")
        return (f"# Tarefa OpenMusic {self.id}: {p.funcao_om} ({p.biblioteca})\n\n{p.descricao}\n\n{arquivo}\n\n"
                f"Entrada (`entrada.txt`): {p.entrada}\n\n```\n{conteudo}\n```\n\n"
                f"Saída esperada: {p.saida}.\n\n{passos}\n")

    def pacote(self) -> bytes:
        """Zip com entrada.txt e instrucoes.md (e o patch, quando houver), para baixar ou levar a outra máquina."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("entrada.txt", self.entrada())
            z.writestr("instrucoes.md", self.instrucoes())
            if self.patch.arquivo and Path(self.patch.arquivo).is_file():
                z.write(self.patch.arquivo, Path(self.patch.arquivo).name)
        return buffer.getvalue()


def criar_tarefa(procedimento: str, parametros: dict[str, float], *, raiz: Path = PASTA_TAREFAS,
                 atual: ir.MusicIR | None = None, plano: Plano | None = None) -> Tarefa:
    """Monta a entrada (só números, pelo formato do patch) antes de criar a pasta: entrada impossível = nada criado."""
    if procedimento not in PATCHES or not PATCHES[procedimento].arquivo:
        raise ValueError(f"não há patch aprovado para {procedimento}")
    conteudo = PATCHES[procedimento].montar(parametros, atual, plano)
    tarefa_id = f"om-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    pasta = raiz / tarefa_id
    tarefa = Tarefa(id=tarefa_id, procedimento=procedimento, parametros=parametros, pasta=str(pasta),
                    conteudo=conteudo)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "entrada.txt").write_text(tarefa.entrada(), encoding="utf-8")
    (pasta / "instrucoes.md").write_text(tarefa.instrucoes(), encoding="utf-8")
    if tarefa.patch.arquivo:
        shutil.copyfile(tarefa.patch.arquivo, pasta / Path(tarefa.patch.arquivo).name)
    return tarefa


# --- importação da saída ---------------------------------------------------------------------------------------------------

def importar(tarefa: Tarefa, saida: bytes | str, *, operacao: str, atual: ir.MusicIR | None = None,
             plano: Plano | None = None, rng: random.Random | None = None) -> ir.MusicIR:
    """Valida a saída (tamanho, texto, só números) e entrega ao formato do patch, que confere quantidade e faixas e
    devolve a MusicIR com proveniência `openmusic`. Levanta ValueError com a mensagem ao usuário."""
    dados = saida if isinstance(saida, bytes) else saida.encode("utf-8")
    if len(dados) > LIMITE_SAIDA:
        raise ValueError(f"a saída tem {len(dados)} bytes; o limite é {LIMITE_SAIDA}")
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("a saída não é texto (UTF-8)") from exc
    pedacos = [p for p in re.split(r"[\s()]+", texto) if p]
    if invalidos := [p for p in pedacos if not _NUMERO.match(p)]:
        raise ValueError(f"a saída tem algo além de números separados por espaço: {invalidos[:3]}")
    valores = [float(x) for x in pedacos]
    pasta = Path(tarefa.pasta)
    if valores == [FALHOU]:
        erro = pasta / "erro.txt"
        detalhe = erro.read_text(encoding="utf-8", errors="replace")[:500] if erro.is_file() else "sem erro.txt"
        raise ValueError(f"o patch falhou no OpenMusic: {detalhe.strip()}")
    p = tarefa.patch
    ctx = Contexto(parametros=tarefa.parametros, entrada=tarefa.entrada(), pasta=pasta, atual=atual, plano=plano,
                   rng=rng or random.Random(0),
                   proveniencia=ir.Proveniencia(origem="openmusic", autor=p.autor, operacao=operacao))
    return p.ler(valores, ctx)

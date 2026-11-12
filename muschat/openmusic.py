"""Bridge OpenMusic, estratégia assistida (ADR 0001): o Muschat prepara a Tarefa (entrada só com números, patch
aprovado, instruções), o compositor roda o patch no OM e devolve o arquivo de saída, que é validado e vira MusicIR.

Nenhum Lisp é gerado nem executado; nada do OM roda a partir daqui.
"""

import io
import math
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from muschat import musicir as ir

# shortcut: relativa à pasta onde o app roda (a raiz do projeto); evoluir para uma pasta configurável se o app
# passar a rodar de outro lugar
PASTA_TAREFAS = Path(".muschat") / "openmusic"  # ignorada pelo git
LIMITE_SAIDA = 64_000  # bytes: uma lista de midicents nunca chega perto disso
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


# --- patches aprovados ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PatchAprovado:
    procedimento: str  # nome no catálogo (o mesmo conceito do procedimento Python, quando houver)
    biblioteca: str  # chave normalizada da biblioteca que o patch chama
    funcao_om: str
    descricao: str
    entrada: str  # formato do entrada.txt
    saida: str  # formato do saida.txt
    passos: tuple[str, ...]  # instruções para montar/rodar no OM
    arquivo: str | None = None  # patch do projeto validado no OpenMusic real


PATCHES: dict[str, PatchAprovado] = {
    "serie_harmonica": PatchAprovado(
        procedimento="serie_harmonica",
        biblioteca="esquisse",
        funcao_om="harm-series",
        descricao="Série harmônica pela Esquisse (harm-series, unit = Midics): devolve alturas em midicents.",
        entrada="uma linha: fundamental_em_midicents numer denom begin end (ex.: 3600 1 1 1 16)",
        saida="saida.txt: a lista de midicents, separados por espaço (parênteses são aceitos)",
        passos=(
            "Extraia o ZIP inteiro para uma pasta; mantenha entrada.txt e serie_harmonica.lisp juntos.",
            "Abra o OpenMusic com o workspace deste projeto.",
            "No Listener, use Lisp › Load File e selecione serie_harmonica.lisp da pasta da tarefa.",
            "Confirme o carregamento da Esquisse se solicitado. O script lê entrada.txt e grava saida.txt "
            "na mesma pasta, usando Midics (1) e Chord (1).",
            "Volte ao Muschat e envie `saida.txt` em “Aguardando você”.",
        ),
        arquivo="patches/serie_harmonica.lisp",
    ),
}


# --- Tarefa ---------------------------------------------------------------------------------------------------------------

# shortcut: entrada, quantidade esperada e importação seguem o formato do único patch (serie_harmonica); um
# segundo patch traz o próprio formato para PatchAprovado
class Tarefa(BaseModel):
    """Pacote de uma etapa assistida: guardado na Composição (sobrevive a rerun e vai no JSON)."""

    id: str
    procedimento: str
    parametros: dict[str, float]
    pasta: str
    criada: datetime = Field(default_factory=datetime.now)

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

    @property
    def esperados(self) -> int:
        return int(self.parametros["parciais"])

    def entrada(self) -> str:
        return f"{int(self.parametros['fundamental'])} 1 1 1 {self.esperados}\n"

    def instrucoes(self) -> str:
        p = self.patch
        passos = "\n".join(f"{n}. {s}" for n, s in enumerate(p.passos, 1))
        arquivo = (f"Patch aprovado: `{p.arquivo}` (copiado nesta pasta)." if p.arquivo else
                   "O patch `.omp` aprovado ainda não está no projeto (o protótipo do ADR 0001 ainda não rodou): "
                   "monte-o com os passos abaixo, sem escrever código Lisp.")
        return (f"# Tarefa OpenMusic {self.id}: {p.funcao_om} ({p.biblioteca})\n\n{p.descricao}\n\n{arquivo}\n\n"
                f"Entrada (`entrada.txt`): {p.entrada}\nConteúdo: `{self.entrada().strip()}`\n\n"
                f"Saída esperada: {p.saida}; {self.esperados} valores.\n\n{passos}\n")

    def pacote(self) -> bytes:
        """Zip com entrada.txt e instrucoes.md (e o patch, quando houver), para baixar ou levar a outra máquina."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("entrada.txt", self.entrada())
            z.writestr("instrucoes.md", self.instrucoes())
            if self.patch.arquivo and Path(self.patch.arquivo).is_file():
                z.write(self.patch.arquivo, Path(self.patch.arquivo).name)
        return buffer.getvalue()


def criar_tarefa(procedimento: str, parametros: dict[str, float], *, raiz: Path = PASTA_TAREFAS) -> Tarefa:
    if procedimento not in PATCHES:
        raise ValueError(f"não há patch aprovado para {procedimento}")
    tarefa_id = f"om-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    pasta = raiz / tarefa_id
    tarefa = Tarefa(id=tarefa_id, procedimento=procedimento, parametros=parametros, pasta=str(pasta))
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "entrada.txt").write_text(tarefa.entrada(), encoding="utf-8")
    (pasta / "instrucoes.md").write_text(tarefa.instrucoes(), encoding="utf-8")
    if tarefa.patch.arquivo:
        shutil.copyfile(tarefa.patch.arquivo, pasta / Path(tarefa.patch.arquivo).name)
    return tarefa


# --- importação da saída ---------------------------------------------------------------------------------------------------

def importar(tarefa: Tarefa, saida: bytes | str, *, operacao: str) -> ir.MusicIR:
    """Valida a saída (tamanho, só números, faixa 0–12700, quantidade esperada) e devolve o material como MusicIR de
    uma parte, uma semínima por altura, com proveniência `openmusic`. Levanta ValueError com a mensagem ao usuário."""
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
    if len(valores) != tarefa.esperados:
        raise ValueError(f"a saída tem {len(valores)} valores; a tarefa pediu {tarefa.esperados}")
    if fora := [v for v in valores if not 0 <= v <= 12700]:
        raise ValueError(f"valores fora de 0–12700 midicents: {fora[:5]}")
    passo = 100 // 2 ** int(tarefa.parametros.get("microtom", 1))
    alturas = [round(v / passo) * passo for v in valores]
    return material(alturas, titulo=f"Série harmônica sobre {tarefa.parametros['fundamental']:.0f} mc (OpenMusic)",
                    proveniencia=ir.Proveniencia(origem="openmusic", autor="OpenMusic/Esquisse", operacao=operacao))


def material(alturas: list[int], *, titulo: str, proveniencia: ir.Proveniencia) -> ir.MusicIR:
    """Alturas como uma voz de semínimas em 4/4; o último compasso se completa com pausa."""
    compassos = max(1, math.ceil(len(alturas) / 4))
    eventos: list[ir.Evento] = [
        ir.Nota(id=f"M1V1E{k + 1}", inicio=Fraction(k, 4), duracao=Fraction(1, 4), proveniencia=proveniencia,
                alturas=[ir.Altura(midicents=a)]) for k, a in enumerate(alturas)]
    resto = Fraction(compassos) - Fraction(len(alturas), 4)
    if resto:
        eventos.append(ir.Pausa(id=f"M1V1E{len(alturas) + 1}", inicio=Fraction(len(alturas), 4), duracao=resto,
                                proveniencia=proveniencia))
    mediana = sorted(alturas)[len(alturas) // 2] if alturas else 6000
    parte = ir.Parte(id="M1", instrumento="Material espectral", clave="sol" if mediana >= 6000 else "fa",
                     vozes=[ir.Voz(id="M1V1", eventos=eventos)])
    return ir.MusicIR(titulo=titulo, compassos=[ir.Compasso(numerador=4, denominador=4, andamento=60 if n == 0 else None)
                                                for n in range(compassos)], partes=[parte])

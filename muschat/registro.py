"""Registro de capacidades: o que cada motor, biblioteca e gravador consegue fazer NESTA máquina, e em que modo.

Detectado em tempo de execução; nada aparece como disponível só por estar planejado (uma biblioteca do OpenMusic só
oferece um procedimento quando está instalada E o patch aprovado dela está no projeto E já rodou no OpenMusic real).
"""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from muschat.notacao.lilypond import Lilypond
from muschat.openmusic import PATCHES, InstalacaoOM, PatchAprovado
from muschat.patches_om import csound
from muschat.procedimentos import CATALOGO

NATIVOS = tuple(n for n, p in CATALOGO.items() if p.funcao is not None)  # o que o motor Python executa

Estado = Literal["indisponivel", "manual", "intercambio_arquivos", "automatico"]
Motor = Literal["python", "openmusic"]
PREFERENCIA: dict[Estado, int] = {"automatico": 0, "intercambio_arquivos": 1, "manual": 2, "indisponivel": 3}
NOME_DO_MOTOR: dict[Motor, str] = {"python": "Python/Abjad", "openmusic": "OpenMusic"}
ROTULO_DO_ESTADO: dict[Estado, str] = {"automatico": "automático", "intercambio_arquivos": "intercâmbio de arquivos",
                                       "manual": "assistido", "indisponivel": "indisponível"}


@dataclass(frozen=True)
class Capacidade:
    nome: str
    tipo: Literal["motor", "biblioteca", "gravacao", "externo"]
    estado: Estado
    motivo: str  # por que está nesse estado (o que falta, quando indisponível)
    versao: str | None = None
    procedimentos: tuple[str, ...] = ()
    dependencias: tuple[str, ...] = ()
    entradas: str = ""
    saidas: str = ""
    limitacoes: str = ""


@dataclass(frozen=True)
class Registro:
    capacidades: tuple[Capacidade, ...]

    def capacidade(self, nome: str) -> Capacidade | None:
        return next((c for c in self.capacidades if c.nome == nome), None)

    def implementacoes(self, procedimento: str) -> dict[Motor, Capacidade]:
        """Motores que executam o procedimento aqui. No OpenMusic, quem declara é a biblioteca, e o estado é o pior
        entre motor e biblioteca."""
        achadas: dict[Motor, Capacidade] = {}
        python = self.capacidade("Python")
        if python and procedimento in python.procedimentos:
            achadas["python"] = python
        om = self.capacidade("OpenMusic")
        libs = [c for c in self.capacidades if c.tipo == "biblioteca" and procedimento in c.procedimentos]
        if om and libs:
            achadas["openmusic"] = max([om, *libs], key=lambda c: PREFERENCIA[c.estado])
        return achadas

    def disponiveis(self) -> list[str]:
        """Procedimentos que algum motor executa aqui (o que vai para o prompt de planejamento)."""
        nomes = dict.fromkeys(p for c in self.capacidades if c.estado != "indisponivel" for p in c.procedimentos)
        return list(nomes)

    @classmethod
    def so_python(cls, procedimentos: tuple[str, ...] = NATIVOS) -> "Registro":
        return cls((_python(procedimentos),))


def _python(procedimentos: tuple[str, ...] = NATIVOS) -> Capacidade:
    return Capacidade("Python", "motor", "automatico", "procedimentos puros sobre a MusicIR, com semente",
                      procedimentos=procedimentos, entradas="MusicIR", saidas="MusicIR",
                      limitacoes="sem ligadura; transpositores em som real")


# nome -> (pasta normalizada em ~/OM/Libraries, executáveis externos de que depende). Listadas mesmo sem patch, para
# o compositor ver o que falta (spec: estado honesto no Registro).
_BIBLIOTECAS_OM = {
    "Esquisse": ("esquisse", ()),
    "Chaos": ("chaos", ()),
    "Situation": ("situation", ()),
    "Clouds": ("clouds", ()),
    "OM2Csound": ("om2csound", ("csound",)),
    "OMChroma": ("omchroma", ()),  # escreve .orc/.sco; o áudio depende da capacidade Csound
    "OM-SuperVP": ("om-supervp", ("supervp",)),
}


def detectar(*, lilypond: Lilypond | None, openmusic: InstalacaoOM | None, abjad_versao: str | None = None,
             which: Callable[[str], str | None] = shutil.which,
             patches: Mapping[str, PatchAprovado] = PATCHES, openmusic_ativo: bool = True) -> Registro:
    caps = [_python(),
            Capacidade("Abjad", "gravacao", "automatico" if abjad_versao else "indisponivel",
                       "MusicIR -> texto LilyPond" if abjad_versao else "pacote abjad não importável",
                       versao=abjad_versao, saidas=".ly"),
            Capacidade("LilyPond", "gravacao", "automatico" if lilypond else "indisponivel",
                       f"em {lilypond.executavel}" if lilypond else
                       "não encontrado: instale o LilyPond >= 2.25.26 ou defina LILYPOND (sem ele, Verovio)",
                       versao=".".join(map(str, lilypond.versao)) if lilypond else None, saidas="PDF, SVG"),
            Capacidade("Verovio", "gravacao", "automatico", "MusicXML -> SVG, sempre disponível (reserva)",
                       saidas="SVG")]
    if not openmusic_ativo:
        return Registro(tuple(caps))
    if openmusic is None:
        caps.append(Capacidade("OpenMusic", "motor", "indisponivel",
                               "não encontrado: instale o OpenMusic 8.0 ou defina OPENMUSIC com o caminho",
                               dependencias=("OpenMusic 8.0",)))
    else:
        caps.append(Capacidade(
            "OpenMusic", "motor", "manual", f"em {openmusic.executavel}; execução assistida (ADR 0001): sem modo "
            "headless documentado, o compositor roda o patch aprovado e devolve o arquivo",
            versao=openmusic.versao, entradas="entrada.txt (números)", saidas="saida.txt (midicents)",
            limitacoes="cada etapa espera o compositor"))
    for nome, exe, uso in (("Csound", "csound", "renderiza o áudio da síntese do OMChroma (.orc/.sco -> .wav)"),
                           ("SuperVP", "supervp", "análise de som do OM-SuperVP (IRCAM Forum, licença própria)")):
        caminho = csound(which) if exe == "csound" else which(exe)
        caps.append(Capacidade(nome, "externo", "automatico" if caminho else "indisponivel",
                               f"em {caminho}; {uso}" if caminho else f"executável {exe} não encontrado no PATH; {uso}"))
    for nome, (chave, externos) in _BIBLIOTECAS_OM.items():
        da_biblioteca = [p for p in patches.values() if p.biblioteca == chave]
        aprovados = tuple(p.procedimento for p in da_biblioteca
                          if p.validado and p.arquivo and Path(p.arquivo).is_file())
        faltando = [e for e in externos if which(e) is None]
        estado: Estado = "indisponivel"
        if openmusic is None:
            motivo = "precisa do OpenMusic"
        elif chave not in openmusic.bibliotecas:
            motivo = "não instalada em ~/OM/Libraries"
        elif faltando:
            motivo = f"instalada, mas falta o executável: {', '.join(faltando)}"
        elif not aprovados:
            motivo = ("instalada; o patch do projeto ainda não foi validado no OpenMusic real" if da_biblioteca
                      else "instalada, mas ainda sem patch aprovado no projeto")
        else:
            estado, motivo = "manual", f"em {openmusic.bibliotecas[chave]}"
        caps.append(Capacidade(nome, "biblioteca", estado, motivo,
                               procedimentos=aprovados if estado != "indisponivel" else (),
                               dependencias=("OpenMusic", *externos)))
    return Registro(tuple(caps))

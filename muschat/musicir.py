"""MusicIR: representação musical intermediária, independente de Abjad, OpenMusic e Verovio.

Tempo em semibreves, como frações exatas (1 = semibreve, 1/4 = semínima). Alturas em midicents (6900 = Lá 4).
Imutável por convenção: toda operação produz uma nova instância.
"""

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator
from itertools import accumulate
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

Dinamica = Literal["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
Clave = Literal["sol", "fa", "do3", "do4", "percussao"]
Letra = Literal["C", "D", "E", "F", "G", "A", "B"]
CLASSE_DA_LETRA: dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
Origem = Literal["llm", "algoritmo", "openmusic", "acervo", "compositor"]
Id = Annotated[str, Field(pattern=r"^[A-Za-z_][\w.-]*$")]  # vale como ID de XML (MusicXML usa o id da parte)
Posicao = Annotated[Fraction, Field(ge=0)]
Duracao = Annotated[Fraction, Field(gt=0)]
Midicents = Annotated[int, Field(ge=0, le=12700)]


class Proveniencia(BaseModel):
    origem: Origem
    autor: str | None = None  # acervo: o `compositor` do _acervo.toml
    operacao: str | None = None  # id da operação que criou/alterou o evento


class Altura(BaseModel):
    midicents: Midicents
    grafia: Letra | None = Field(default=None, description="Letra preferida na escrita (enarmonia, quarto de tom).")


class _Evento(BaseModel):
    id: Id
    inicio: Posicao
    duracao: Duracao
    proveniencia: Proveniencia

    @property
    def fim(self) -> Fraction:
        return self.inicio + self.duracao


class Nota(_Evento):
    """Nota ou acorde (várias alturas)."""

    tipo: Literal["nota"] = "nota"
    alturas: list[Altura] = Field(min_length=1)
    tecnicas: list[str] = Field(default_factory=list, description="Nomes do grimório (ver notacao.tecnicas).")
    dinamica: Dinamica | None = None
    texto: str | None = None
    glissando: bool = Field(default=False, description="Glissando até a próxima nota da voz.")


class Pausa(_Evento):
    tipo: Literal["pausa"] = "pausa"
    dinamica: Dinamica | None = None
    texto: str | None = None


class Eletronico(_Evento):
    """Disparo/cue eletrônico."""

    tipo: Literal["eletronico"] = "eletronico"
    duracao: Posicao  # disparo pontual tem duração zero
    descricao: str


class Curva(_Evento):
    """Curva de controle de um parâmetro contínuo; pontos (tempo relativo ao início, valor)."""

    tipo: Literal["curva"] = "curva"
    parametro: str
    pontos: list[tuple[Posicao, float]] = Field(min_length=1)


class Parcial(BaseModel):
    frequencia: float = Field(gt=0)
    amplitude: float = Field(ge=0)
    fase: float = 0.0


class Espectral(_Evento):
    """Dado espectral: nunca vira nota falsa."""

    tipo: Literal["espectral"] = "espectral"
    parciais: list[Parcial] = Field(min_length=1)


Evento = Annotated[Nota | Pausa | Eletronico | Curva | Espectral, Field(discriminator="tipo")]


class Quialtera(BaseModel):
    """`numerador` no tempo de `denominador` (3:2 = tercina). Aninhável por `pai`."""

    id: Id
    numerador: int = Field(gt=0)
    denominador: int = Field(gt=0)
    eventos: list[Id] = Field(min_length=1)
    pai: Id | None = None


class Voz(BaseModel):
    id: Id
    eventos: list[Evento] = Field(default_factory=list)
    quialteras: list[Quialtera] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coerente(self) -> "Voz":
        for a, b in zip(self.eventos, self.eventos[1:]):
            if b.inicio < a.fim:
                raise ValueError(f"eventos {a.id} e {b.id} se sobrepõem ou estão fora de ordem na voz {self.id}")
        ids = {e.id for e in self.eventos}
        pais = {q.id: q.pai for q in self.quialteras}
        for q in self.quialteras:
            if not set(q.eventos) <= ids or (q.pai is not None and q.pai not in pais):
                raise ValueError(f"quiáltera {q.id} aponta para evento ou grupo inexistente na voz {self.id}")
            vistos, g = {q.id}, q.pai
            while g is not None:
                if g in vistos:
                    raise ValueError(f"quiálteras em ciclo na voz {self.id}: {q.id}")
                vistos.add(g)
                g = pais[g]
        membros = [i for q in self.quialteras for i in q.eventos]
        if len(membros) != len(set(membros)):
            raise ValueError(f"evento em mais de um grupo de quiáltera na voz {self.id} (use `pai` para aninhar)")
        return self


class Parte(BaseModel):
    id: Id
    instrumento: str
    clave: Clave = "sol"
    tessitura: tuple[Midicents, Midicents] | None = Field(default=None, description="(grave, agudo) em midicents.")
    vozes: list[Voz] = Field(min_length=1)

    @model_validator(mode="after")
    def _tessitura_ordenada(self) -> "Parte":
        if self.tessitura and self.tessitura[0] > self.tessitura[1]:
            raise ValueError(f"tessitura da parte {self.id} invertida: {self.tessitura}")
        return self


class Compasso(BaseModel):
    numerador: int = Field(gt=0)
    denominador: int = Field(gt=0)
    andamento: int | None = Field(default=None, gt=0, description="Semínimas por minuto a partir deste compasso.")
    # shortcut: compasso incompleto/excedente herdado do exemplo do LLM; some quando houver validação métrica
    duracao: Fraction | None = Field(default=None, ge=0)

    @property
    def comprimento(self) -> Fraction:
        return self.duracao if self.duracao is not None else Fraction(self.numerador, self.denominador)


class MusicIR(BaseModel):
    titulo: str
    compositor: str | None = None
    compassos: list[Compasso] = Field(min_length=1)
    partes: list[Parte] = Field(min_length=1)

    def inicios(self) -> list[Fraction]:
        """Posição de início de cada compasso."""
        return list(accumulate((c.comprimento for c in self.compassos[:-1]), initial=Fraction(0)))

    def eventos(self) -> Iterator[tuple[Parte, Voz, Evento]]:
        for parte in self.partes:
            for voz in parte.vozes:
                for evento in voz.eventos:
                    yield parte, voz, evento

    @model_validator(mode="after")
    def _ids_unicos(self) -> "MusicIR":
        ids = [p.id for p in self.partes] + [v.id for p in self.partes for v in p.vozes]
        ids += [e.id for _, _, e in self.eventos()]
        repetidos = [i for i, n in Counter(ids).items() if n > 1]
        if repetidos:
            raise ValueError(f"ids repetidos: {repetidos}")
        return self


def localizar(ir: MusicIR) -> dict[str, tuple[str, str | None, int | None]]:
    """id -> (parte, voz, compasso 1..n); partes e vozes entram com voz/compasso None."""
    inicios = ir.inicios()
    locais: dict[str, tuple[str, str | None, int | None]] = {}
    for p in ir.partes:
        locais[p.id] = (p.id, None, None)
        for v in p.vozes:
            locais[v.id] = (p.id, v.id, None)
            for e in v.eventos:
                locais[e.id] = (p.id, v.id, bisect_right(inicios, e.inicio))
    return locais


class Diferenca(BaseModel):
    adicionados: list[str] = Field(default_factory=list)
    removidos: list[str] = Field(default_factory=list)
    alterados: dict[str, list[str]] = Field(default_factory=dict, description="id -> campos alterados")
    compassos: list[int] = Field(default_factory=list, description="números (1, 2...) dos compassos alterados")
    locais: dict[str, tuple[str, int]] = Field(default_factory=dict, description="id do evento -> (parte, compasso)")

    def __bool__(self) -> bool:
        return bool(self.adicionados or self.removidos or self.alterados or self.compassos)


def _por_id(ir: MusicIR) -> dict[str, dict]:
    """Partes, vozes e eventos por id, sem os filhos; o pai entra como campo (`parte`, `voz`)."""
    itens: dict[str, dict] = {}
    for p in ir.partes:
        itens[p.id] = p.model_dump(exclude={"id", "vozes"})
        for v in p.vozes:
            itens[v.id] = {**v.model_dump(exclude={"id", "eventos"}), "parte": p.id}
            for e in v.eventos:
                itens[e.id] = {**e.model_dump(exclude={"id"}), "voz": v.id}
    return itens


def comparar(antes: MusicIR, depois: MusicIR) -> Diferenca:
    """Diferença estrutural por id (partes, vozes, eventos) e por número de compasso."""
    a, b = _por_id(antes), _por_id(depois)
    ca, cb = antes.compassos, depois.compassos
    alterados = {i: [c for c in a[i].keys() | b[i].keys() if a[i].get(c) != b[i].get(c)] for i in a.keys() & b.keys()}
    alterados = {i: sorted(campos) for i, campos in alterados.items() if campos}
    adicionados, removidos = [i for i in b if i not in a], [i for i in a if i not in b]
    locais = {**localizar(antes), **localizar(depois)}  # o lugar novo vale para eventos que mudaram de compasso
    return Diferenca(
        adicionados=adicionados,
        removidos=removidos,
        alterados=alterados,
        locais={i: (parte, compasso) for i in [*adicionados, *removidos, *alterados]
                for parte, _, compasso in [locais[i]] if compasso is not None},
        compassos=[n for n in range(1, max(len(ca), len(cb)) + 1)
                   if n > min(len(ca), len(cb)) or ca[n - 1] != cb[n - 1]],
    )

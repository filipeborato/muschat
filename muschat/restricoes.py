"""Verificador único de restrições: roda depois de qualquer operação e aponta cada violação com o local."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from muschat.musicir import MusicIR, Nota, localizar


class Restricao(BaseModel):
    tipo: Literal["tessitura", "intervalo_proibido", "sem_unissono", "densidade_maxima"]
    valor: float | None = Field(default=None, description="intervalo_proibido: cents; densidade_maxima: ataques por "
                                "semínima; tessitura e sem_unissono não usam")
    grave: int | None = Field(default=None, description="tessitura: limite grave em midicents")
    agudo: int | None = Field(default=None, description="tessitura: limite agudo em midicents")
    partes: list[str] = Field(default_factory=list, description="ids das partes; vazio = todas")

    @model_validator(mode="after")
    def _com_valor(self) -> "Restricao":
        if self.tipo in ("intervalo_proibido", "densidade_maxima") and self.valor is None:
            raise ValueError(f"restrição {self.tipo} precisa de valor")
        if self.tipo == "tessitura" and (self.grave is None or self.agudo is None or self.grave > self.agudo):
            raise ValueError("restrição tessitura precisa de grave <= agudo")
        return self


@dataclass(frozen=True)
class Violacao:
    restricao: str
    parte: str
    compasso: int | None
    evento: str | None
    detalhe: str

    def __str__(self) -> str:
        onde = " ".join(x for x in (self.parte, f"c.{self.compasso}" if self.compasso else "", self.evento or "") if x)
        return f"{self.restricao} ({onde}): {self.detalhe}"


def verificar(ir: MusicIR, restricoes: list[Restricao]) -> list[Violacao]:
    """A tessitura declarada em cada parte vale sempre; as outras restrições só quando pedidas."""
    locais = localizar(ir)
    saida: list[Violacao] = []

    def violar(tipo: str, parte: str, evento: str | None, detalhe: str, compasso: int | None = None) -> None:
        saida.append(Violacao(tipo, parte, compasso if evento is None else locais[evento][2], evento, detalhe))

    faixas = [(p, p.tessitura) for p in ir.partes if p.tessitura]  # a da parte vale sempre
    faixas += [(p, (r.grave, r.agudo)) for r in restricoes if r.tipo == "tessitura" and r.grave is not None
               and r.agudo is not None for p in ir.partes if not r.partes or p.id in r.partes]
    for p, (grave, agudo) in faixas:
        for v in p.vozes:
            for e in v.eventos:
                for a in e.alturas if isinstance(e, Nota) else []:
                    if not grave <= a.midicents <= agudo:
                        violar("tessitura", p.id, e.id, f"{a.midicents} mc fora de {grave}–{agudo}")

    for r in restricoes:
        partes = [p for p in ir.partes if not r.partes or p.id in r.partes]
        if r.tipo == "intervalo_proibido":
            for p in partes:
                for v in p.vozes:
                    notas = [e for e in v.eventos if isinstance(e, Nota)]
                    for anterior, nota in zip(notas, notas[1:]):
                        if abs(nota.alturas[0].midicents - anterior.alturas[0].midicents) == r.valor:
                            violar(r.tipo, p.id, nota.id, f"intervalo de {r.valor:g} cents depois de {anterior.id}")
        elif r.tipo == "sem_unissono":
            soando = sorted(((e, p.id) for p in partes for v in p.vozes for e in v.eventos if isinstance(e, Nota)),
                            key=lambda x: x[0].inicio)
            for i, (primeira, parte_a) in enumerate(soando):
                for segunda, parte_b in soando[i + 1:]:
                    if segunda.inicio >= primeira.fim:
                        break
                    comum = {x.midicents for x in primeira.alturas} & {x.midicents for x in segunda.alturas}
                    if parte_a != parte_b and comum:
                        violar(r.tipo, parte_b, segunda.id, f"uníssono {sorted(comum)} mc com {primeira.id} ({parte_a})")
        elif r.tipo == "densidade_maxima":
            assert r.valor is not None
            for p in partes:
                for v in p.vozes:
                    contagem: dict[int, int] = {}
                    for e in v.eventos:
                        n = locais[e.id][2]
                        if isinstance(e, Nota) and n is not None and n <= len(ir.compassos):
                            contagem[n] = contagem.get(n, 0) + 1
                    for n, ataques in sorted(contagem.items()):
                        por_seminima = ataques / float(ir.compassos[n - 1].comprimento * 4)
                        if por_seminima > r.valor:
                            violar(r.tipo, p.id, None, f"{por_seminima:g} ataques por semínima em {v.id} "
                                   f"(máximo {r.valor:g})", compasso=n)
    return saida

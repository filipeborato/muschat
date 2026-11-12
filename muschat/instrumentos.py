"""Tabela única de instrumentos: família (liga às técnicas), clave e tessitura em som real (midicents).

A MusicIR guarda som real: transpositores (clarinetes, trompas, saxofones, contrabaixo, piccolo...) aparecem em som
real, e piano, harpa, marimba e celesta numa pauta só.
# shortcut: som real e pauta única; evoluir com `transposicao` e pauta dupla na Parte quando a notação pedir.
"""

import unicodedata
from dataclasses import dataclass
from typing import Literal

from muschat.musicir import Clave

Familia = Literal["cordas", "madeiras", "metais", "percussao", "teclado", "harpa", "violao", "voz"]
FAMILIAS: tuple[Familia, ...] = ("cordas", "madeiras", "metais", "percussao", "teclado", "harpa", "violao", "voz")
LINHA_CENTRAL = 7100  # percussão sem altura definida: Si 4, a linha do meio da pauta


@dataclass(frozen=True)
class Instrumento:
    familia: Familia
    clave: Clave
    tessitura: tuple[int, int]

    @property
    def altura_definida(self) -> bool:
        return self.tessitura[0] != self.tessitura[1]


def _sem_altura() -> Instrumento:
    return Instrumento("percussao", "percussao", (LINHA_CENTRAL, LINHA_CENTRAL))


INSTRUMENTOS: dict[str, Instrumento] = {
    # cordas friccionadas
    "violino": Instrumento("cordas", "sol", (5500, 10300)),
    "viola": Instrumento("cordas", "do3", (4800, 9100)),
    "violoncelo": Instrumento("cordas", "fa", (3600, 8100)),
    "contrabaixo": Instrumento("cordas", "fa", (2800, 6700)),
    # madeiras
    "piccolo": Instrumento("madeiras", "sol", (7400, 10800)),
    "flauta": Instrumento("madeiras", "sol", (6000, 9800)),
    "flauta contralto": Instrumento("madeiras", "sol", (5500, 9100)),
    "flauta baixo": Instrumento("madeiras", "sol", (4800, 8400)),
    "oboe": Instrumento("madeiras", "sol", (5800, 9300)),
    "oboe d'amore": Instrumento("madeiras", "sol", (5600, 8800)),
    "corne ingles": Instrumento("madeiras", "sol", (5200, 8400)),
    "requinta": Instrumento("madeiras", "sol", (5500, 9500)),
    "clarinete": Instrumento("madeiras", "sol", (5000, 9400)),
    "clarinete baixo": Instrumento("madeiras", "fa", (3400, 7700)),
    "clarone": Instrumento("madeiras", "fa", (3400, 7700)),
    "clarinete contrabaixo": Instrumento("madeiras", "fa", (2200, 5800)),
    "fagote": Instrumento("madeiras", "fa", (3400, 7600)),
    "contrafagote": Instrumento("madeiras", "fa", (2200, 5500)),
    "saxofone soprano": Instrumento("madeiras", "sol", (5600, 8800)),
    "saxofone alto": Instrumento("madeiras", "sol", (4900, 8100)),
    "saxofone tenor": Instrumento("madeiras", "fa", (4400, 7600)),
    "saxofone baritono": Instrumento("madeiras", "fa", (3600, 6900)),
    # metais
    "trompa": Instrumento("metais", "fa", (3500, 7700)),
    "trompete": Instrumento("metais", "sol", (5400, 8600)),
    "trompete piccolo": Instrumento("metais", "sol", (6100, 9300)),
    "cornet": Instrumento("metais", "sol", (5400, 8400)),
    "flugelhorn": Instrumento("metais", "sol", (5200, 8100)),
    "trombone": Instrumento("metais", "fa", (4000, 7700)),
    "trombone baixo": Instrumento("metais", "fa", (3400, 7000)),
    "eufonio": Instrumento("metais", "fa", (3400, 7000)),
    "tuba": Instrumento("metais", "fa", (2600, 6500)),
    # percussão com altura definida
    "timpanos": Instrumento("percussao", "fa", (3800, 6000)),
    "marimba": Instrumento("percussao", "sol", (3600, 9600)),
    "xilofone": Instrumento("percussao", "sol", (6500, 10800)),
    "vibrafone": Instrumento("percussao", "sol", (5300, 8900)),
    "glockenspiel": Instrumento("percussao", "sol", (7900, 10800)),
    "crotalos": Instrumento("percussao", "sol", (8400, 10800)),
    "sinos tubulares": Instrumento("percussao", "sol", (6000, 7700)),
    # percussão sem altura definida
    "caixa": _sem_altura(),
    "bumbo": _sem_altura(),
    "pratos": _sem_altura(),
    "prato suspenso": _sem_altura(),
    "tam-tam": _sem_altura(),
    "gongo": _sem_altura(),
    "triangulo": _sem_altura(),
    "tom-tons": _sem_altura(),
    "bongos": _sem_altura(),
    "congas": _sem_altura(),
    "wood block": _sem_altura(),
    "temple blocks": _sem_altura(),
    "pandeiro": _sem_altura(),
    "castanholas": _sem_altura(),
    "chocalho": _sem_altura(),
    "guiro": _sem_altura(),
    "chicote": _sem_altura(),
    # teclados
    "piano": Instrumento("teclado", "sol", (2100, 10800)),
    "celesta": Instrumento("teclado", "sol", (6000, 10800)),
    "cravo": Instrumento("teclado", "sol", (2900, 8900)),
    "orgao": Instrumento("teclado", "sol", (2400, 9600)),
    "acordeao": Instrumento("teclado", "sol", (4000, 9300)),
    # cordas dedilhadas
    "harpa": Instrumento("harpa", "sol", (2300, 10400)),
    "violao": Instrumento("violao", "sol", (4000, 8300)),
    "guitarra": Instrumento("violao", "sol", (4000, 8600)),
    "bandolim": Instrumento("violao", "sol", (5500, 8800)),
    # vozes
    "soprano": Instrumento("voz", "sol", (6000, 8400)),
    "mezzo-soprano": Instrumento("voz", "sol", (5700, 8100)),
    "contralto": Instrumento("voz", "sol", (5300, 7700)),
    "contratenor": Instrumento("voz", "sol", (5500, 7700)),
    "tenor": Instrumento("voz", "sol", (4800, 7200)),
    "baritono": Instrumento("voz", "fa", (4300, 6700)),
    "baixo": Instrumento("voz", "fa", (4000, 6400)),
}
_SINONIMOS = {
    "sax ": "saxofone ", "flautim": "piccolo", "corno ingles": "corne ingles", "clarinete em mi": "requinta",
    "bass clarinet": "clarinete baixo", "timbales": "timpanos",
    "trompa em": "trompa", "corne frances": "trompa", "trumpet": "trompete", "trombone tenor": "trombone",
    "bombardino": "eufonio", "lira": "glockenspiel", "vibrafono": "vibrafone",
    "caixa clara": "caixa", "gran cassa": "bumbo", "bombo": "bumbo", "triangle": "triangulo", "tamtam": "tam-tam",
    "chimes": "sinos tubulares", "campanas": "sinos tubulares", "harpsichord": "cravo", "acordeon": "acordeao",
    "guitarra eletrica": "guitarra", "mezzo soprano": "mezzo-soprano", "timpano ": "timpanos ",
}


def _normalizar(nome: str) -> str:
    texto = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower().strip() + " "
    for de, para in _SINONIMOS.items():
        texto = texto.replace(unicodedata.normalize("NFKD", de).encode("ascii", "ignore").decode(), para)
    return texto


def instrumento(nome: str) -> Instrumento:
    return INSTRUMENTOS[identificar(nome)]


def identificar(nome: str) -> str:
    """Chave da tabela para um nome livre ("Violino I", "Clarinete baixo em Si bemol"): o nome conhecido mais
    longo contido nele vence (clarinete baixo antes de clarinete e de baixo)."""
    chave = _normalizar(nome)
    for conhecido in sorted(INSTRUMENTOS, key=len, reverse=True):
        if conhecido in chave:
            return conhecido
    raise ValueError(f"instrumento sem tessitura conhecida: {nome!r} (conhecidos: {', '.join(INSTRUMENTOS)})")


def no_registro(altura: int, tessitura: tuple[int, int] | None) -> int:
    """Sobe/desce oitavas até caber na tessitura; tessitura menor que uma oitava (percussão sem altura) prende."""
    if tessitura is None:
        return altura
    grave, agudo = tessitura
    if agudo - grave < 1200:
        return min(max(altura, grave), agudo)
    while altura < grave:
        altura += 1200
    while altura > agudo:
        altura -= 1200
    return altura

"""Partitura simbólica compacta que o modelo preenche (response_schema) e o builder vira MusicXML."""

from typing import Literal

from pydantic import BaseModel, Field

Duracao = Literal["inteira", "minima", "seminima", "colcheia", "semicolcheia"]
Dinamica = Literal["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
Clave = Literal["sol", "fa", "do3", "do4", "percussao"]


class Nota(BaseModel):
    alturas: list[str] = Field(
        default_factory=list,
        description='Alturas em notação científica: "C4", "F#5", "Bb3"; quarto de tom com + ou - '
        'no fim ("A4+", "A4-"). Várias alturas = acorde/multifônico; lista vazia = pausa.',
    )
    duracao: Duracao
    pontuada: bool = False
    tecnicas: list[str] = Field(default_factory=list, description="Técnicas da lista do prompt, em italiano.")
    dinamica: Dinamica | None = None
    texto: str | None = Field(default=None, description="Indicação textual extra acima da nota.")
    glissando: bool = Field(default=False, description="Glissando desta nota até a próxima.")


class Compasso(BaseModel):
    notas: list[Nota]


class Partitura(BaseModel):
    titulo: str
    instrumento: str
    clave: Clave = "sol"
    metrica: str = "4/4"
    andamento: int | None = None
    compassos: list[Compasso]

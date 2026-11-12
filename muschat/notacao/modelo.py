"""Partitura simbólica compacta que o modelo preenche (response_schema) e o adaptador vira MusicIR."""

import re
from fractions import Fraction
from typing import Literal, cast

from pydantic import BaseModel, Field

from muschat import musicir as ir
from muschat.musicir import CLASSE_DA_LETRA, Clave, Dinamica

Duracao = Literal["inteira", "minima", "seminima", "colcheia", "semicolcheia"]


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


_FIGURA = {"inteira": Fraction(1), "minima": Fraction(1, 2), "seminima": Fraction(1, 4),
           "colcheia": Fraction(1, 8), "semicolcheia": Fraction(1, 16)}
_ALTER = {"#": 1, "##": 2, "b": -1, "bb": -2, "+": 0.5, "-": -0.5, "#+": 1.5, "b-": -1.5}
_ALTURA = re.compile(r"^([A-G])(##|#\+|#|bb|b-|b|\+|-)?(\d)([+-])?$")


def altura(texto: str) -> ir.Altura | None:
    """"A4+" -> midicents 6950 com grafia A; None se a notação for inválida."""
    m = _ALTURA.match(texto.strip())
    if not m:
        return None
    letra, acidente, oitava, quarto = m.groups()
    if quarto and acidente and acidente[-1] in "+-":
        return None  # quarto de tom escrito duas vezes ("A#+4+"): ambíguo
    alter = _ALTER.get(acidente or "", 0) + _ALTER.get(quarto or "", 0)
    midicents = round((12 * (int(oitava) + 1) + CLASSE_DA_LETRA[letra] + alter) * 100)
    return ir.Altura(midicents=midicents, grafia=cast(ir.Letra, letra)) if 0 <= midicents <= 12700 else None


def para_ir(p: Partitura, proveniencia: ir.Proveniencia | None = None) -> ir.MusicIR:
    """Exemplo ilustrativo -> MusicIR de uma parte e uma voz; ids determinísticos (P1, P1V1, P1V1E1...)."""
    prov = proveniencia or ir.Proveniencia(origem="llm")
    texto_num, _, texto_den = p.metrica.partition("/")
    valida = texto_num.isdigit() and texto_den.isdigit() and int(texto_num) > 0 and int(texto_den) > 0
    num, den = (int(texto_num), int(texto_den)) if valida else (4, 4)  # métrica inválida: 4/4, como antes
    andamento = p.andamento if p.andamento and p.andamento > 0 else None
    eventos: list[ir.Evento] = []
    compassos: list[ir.Compasso] = []
    inicio = Fraction(0)
    for c in p.compassos:
        comeco = inicio
        for nota in c.notas:
            duracao = _FIGURA[nota.duracao] * (Fraction(3, 2) if nota.pontuada else 1)
            alturas = [a for a in map(altura, nota.alturas) if a is not None]  # inválida: some (tudo inválido = pausa)
            eid = f"P1V1E{len(eventos) + 1}"
            if alturas:
                eventos.append(ir.Nota(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov, alturas=alturas,
                                       tecnicas=nota.tecnicas, dinamica=nota.dinamica, texto=nota.texto,
                                       glissando=nota.glissando))
            else:
                eventos.append(ir.Pausa(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov,
                                        dinamica=nota.dinamica, texto=nota.texto))
            inicio += duracao
        real = inicio - comeco
        compassos.append(ir.Compasso(numerador=num, denominador=den, andamento=None if compassos else andamento,
                                     duracao=None if real == Fraction(num, den) else real))
    voz = ir.Voz(id="P1V1", eventos=eventos)
    return ir.MusicIR(titulo=p.titulo, compassos=compassos,
                      partes=[ir.Parte(id="P1", instrumento=p.instrumento, clave=p.clave, vozes=[voz])])

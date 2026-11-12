"""MusicIR de exemplo compartilhada pelos testes."""

from fractions import Fraction as F

from muschat.musicir import (
    Altura, Compasso, Curva, Espectral, MusicIR, Nota, Parcial, Parte, Pausa, Proveniencia, Quialtera, Voz,
)

LLM = Proveniencia(origem="llm")


def quarteto() -> MusicIR:
    violino = Parte(id="vln", instrumento="Violino", vozes=[Voz(id="vln1", eventos=[
        Nota(id="a", inicio=F(0), duracao=F(1, 2), alturas=[Altura(midicents=6950, grafia="A")],
             tecnicas=["sul ponticello"], dinamica="pp", proveniencia=LLM),
        Pausa(id="b", inicio=F(1, 2), duracao=F(1, 2), proveniencia=LLM),
    ])])
    cello = Parte(id="vc", instrumento="Violoncelo", clave="fa", tessitura=(3600, 7600), vozes=[
        Voz(id="vc1", eventos=[
            Nota(id="c", inicio=F(0), duracao=F(1, 6), alturas=[Altura(midicents=3600)], proveniencia=LLM),
            Nota(id="d", inicio=F(1, 6), duracao=F(1, 6), alturas=[Altura(midicents=4300)], proveniencia=LLM),
            Nota(id="e", inicio=F(1, 3), duracao=F(1, 6), alturas=[Altura(midicents=4800)], proveniencia=LLM),
            Pausa(id="f", inicio=F(1, 2), duracao=F(1, 2), proveniencia=LLM),
        ], quialteras=[Quialtera(id="q1", numerador=3, denominador=2, eventos=["c", "d", "e"])]),
        Voz(id="vc2", eventos=[
            Curva(id="g", inicio=F(0), duracao=F(1), parametro="filtro", pontos=[(F(0), 0.0), (F(1), 1.0)],
                  proveniencia=Proveniencia(origem="algoritmo", operacao="op1")),
        ]),
        Voz(id="vc3", eventos=[
            Espectral(id="h", inicio=F(0), duracao=F(1), parciais=[Parcial(frequencia=65.4, amplitude=1.0)],
                      proveniencia=Proveniencia(origem="openmusic")),
        ]),
    ])
    return MusicIR(titulo="Quarteto", compassos=[Compasso(numerador=4, denominador=4, andamento=60)],
                   partes=[violino, cello])

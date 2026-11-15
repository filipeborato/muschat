import math
import random
import xml.etree.ElementTree as ET
from fractions import Fraction as F
from pathlib import Path

import pytest

from muschat.musicir import Nota, Proveniencia
from muschat.notacao.lilypond import para_lilypond
from muschat.notacao.musicxml import grafia, para_musicxml
from muschat.patches_om import Contexto, PATCHES, material
from tests.test_procedimentos import _rodar


LETRAS = 'C C G C E G B C D E F G A B B C D D E E F F G G G A A B B B B C'.split()


@pytest.mark.parametrize('fundamental,deslocamento', [(3600, 0), (3800, 1), (4100, 3), (3650, 0)])
@pytest.mark.parametrize('microtom', [0, 1, 2])
def test_importacao_harmonica_grafa_os_32_parciais(fundamental, deslocamento, microtom):
    valores = [fundamental + 1200 * math.log2(n) for n in range(1, 33)]
    ctx = Contexto(dict(fundamental=fundamental, parciais=32, microtom=microtom), '', Path('.'),
                   None, None, random.Random(1), Proveniencia(origem='openmusic'))
    ir = PATCHES['serie_harmonica'].ler(valores, ctx)
    alturas = [e.alturas[0] for _, _, e in ir.eventos() if isinstance(e, Nota)]
    passo = 100 // 2 ** microtom
    assert [a.midicents for a in alturas] == [round(v / passo) * passo for v in valores]
    assert [a.grafia for a in alturas] == ['CDEFGAB'[('CDEFGAB'.index(l) + deslocamento) % 7] for l in LETRAS]
    if fundamental == 3600 and microtom == 1:
        assert grafia(alturas[6]) == ('B', F(-3, 2), 4)
        assert grafia(alturas[10]) == ('F', F(1, 2), 5)
        assert grafia(alturas[12]) == ('A', F(-1, 2), 5)
        notas = ET.fromstring(para_musicxml(ir)).findall('.//note')
        for indice, letra, alter, acidente in [(6, 'B', '-1.5', 'three-quarters-flat'),
                                               (10, 'F', '0.5', 'quarter-sharp'),
                                               (12, 'A', '-0.5', 'quarter-flat')]:
            assert notas[indice].findtext('pitch/step') == letra
            assert notas[indice].findtext('pitch/alter') == alter
            assert notas[indice].findtext('accidental') == acidente
        ly = para_lilypond(ir)['grade']
        assert "btqf'4" in ly and "fqs''4" in ly and "aqf''4" in ly


def test_serie_harmonica_preserva_grafia_ao_mudar_oitavas():
    ir = _rodar('serie_harmonica', fundamental=3600, parciais=16, microtom=1, compassos=32, pausas=0)
    esperadas = {round((3600 + 1200 * math.log2(n)) / 50) * 50 % 1200: l
                 for n, l in enumerate(LETRAS[:16], 1)}
    alturas = [a for _, _, e in ir.eventos() if isinstance(e, Nota) for a in e.alturas]
    assert {a.midicents % 1200 for a in alturas} == set(esperadas)
    assert all(a.grafia == esperadas[a.midicents % 1200] for a in alturas)
    orquestrada = _rodar('orquestrar_material', ir)
    assert all(a.grafia == esperadas[a.midicents % 1200] for _, _, e in orquestrada.eventos()
               if isinstance(e, Nota) for a in e.alturas)


def test_espectralizar_preserva_grafia_em_todas_as_alturas_do_acorde():
    atual = material([6950, 7750, 8050], titulo='Espectro', proveniencia=Proveniencia(origem='compositor'))
    for _, _, e in atual.eventos():
        if isinstance(e, Nota):
            e.alturas.append(e.alturas[0])
    ir = _rodar('espectralizar', atual, fundamental=3600, parciais=16, microtom=1)
    esperadas = {round((3600 + 1200 * math.log2(n)) / 50) * 50: l
                 for n, l in enumerate(LETRAS[:16], 1)}
    assert all(a.grafia == esperadas[a.midicents] for _, _, e in ir.eventos()
               if isinstance(e, Nota) for a in e.alturas)

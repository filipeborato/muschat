import io
import json
import struct
import zipfile
from fractions import Fraction as F

from muschat.exportacao import experimento_json, metricas, pacote, para_midi
from muschat.notacao.lilypond import Gravacao
from tests.exemplos import quarteto


def _ler_midi(dados: bytes) -> tuple[int, list[list[tuple[int, int, int]]]]:
    """Leitor mínimo de SMF: (ticks por semínima, [(início, altura, duração) por faixa])."""
    assert dados[:4] == b"MThd"
    _, n, tpq = struct.unpack(">HHH", dados[8:14])
    pos, faixas = 14, []
    for _ in range(n):
        assert dados[pos:pos + 4] == b"MTrk"
        fim = pos + 8 + struct.unpack(">I", dados[pos + 4:pos + 8])[0]
        pos, t, status, abertas, notas = pos + 8, 0, 0, {}, []
        while pos < fim:
            delta = 0
            while True:
                b = dados[pos]; pos += 1
                delta = (delta << 7) | (b & 0x7F)
                if b < 0x80:
                    break
            t += delta
            if dados[pos] >= 0x80:
                status = dados[pos]; pos += 1
            if status == 0xFF:
                pos += 1
                tamanho = dados[pos]; pos += 1 + tamanho
            elif status & 0xF0 in (0x80, 0x90):
                altura, vel = dados[pos], dados[pos + 1]; pos += 2
                if status & 0xF0 == 0x90 and vel:
                    abertas[altura] = t
                else:
                    notas.append((abertas.pop(altura), altura, t))
            elif status & 0xF0 in (0xC0, 0xD0):
                pos += 1
            else:
                pos += 2
        faixas.append(sorted((i, a, f - i) for i, a, f in notas))
    return tpq, faixas


def test_midi_relido_tem_as_alturas_e_duracoes_esperadas():
    tpq, faixas = _ler_midi(para_midi(quarteto()))
    semibreve = 4 * tpq
    violino, cello = faixas
    # Lá 4 + 50 cents arredonda para o semitom mais próximo (perda declarada); a pausa não vira nota
    assert violino == [(0, 70, semibreve // 2)]
    # tercinas: 1/6 de semibreve cada; curva e dado espectral não viram notas
    assert cello == [(int(F(k, 6) * semibreve), a, semibreve // 6) for k, a in enumerate([36, 43, 48])]


def test_metricas_descritivas_conferem_na_musicir_feita_a_mao():
    ir = quarteto()  # notas: Lá4+50c (1/2), tercinas Dó2, Sol2, Dó3 (1/6 cada); 1 compasso 4/4; 8 eventos
    m = metricas(ir)
    assert m["eventos"] == 8 and m["notas"] == 4
    assert m["ataques_por_seminima_somando_partes"] == 1.0  # 4 ataques em 4 semínimas
    assert m["ambito_midicents"] == [3600, 6950]
    assert m["duracoes"] == {"1/6": 3, "1/2": 1}
    assert m["desvio_intervalar_cents"] == 100.0  # intervalos 700 e 500 no violoncelo
    assert "violacoes" not in m and "diff" not in m


def test_metricas_com_versao_anterior_e_restricoes():
    antes = quarteto()
    depois = antes.model_copy(deep=True)
    depois.partes[0].vozes[0].eventos[0].dinamica = "ff"
    m = metricas(depois, anterior=antes, violacoes=["tessitura (vc c.1 c): grave"])
    assert m["diff"] == {"adicionados": 0, "removidos": 0, "alterados": 1}
    assert m["violacoes"] == 1


def _nomes(dados: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        return set(z.namelist())


def test_zip_contem_so_os_formatos_efetivamente_gerados():
    from tests.test_composicao import _quarteto
    c = _quarteto()
    base = {"composicao_v1.musicxml", "composicao_v1.mid", "composicao.json", "experimento.json", "PERDAS.txt"}
    assert _nomes(pacote(c, 0)) == base  # sem LilyPond nem Verovio: nada de .ly, PDF ou SVG
    com_verovio = pacote(c, 0, svg="<svg/>")
    assert _nomes(com_verovio) == base | {"composicao_v1.svg"}
    sem_gravacao = pacote(c, 0, lilypond={"grade": "x"}, gravacao=Gravacao(aviso="sem LilyPond"), svg="<svg/>")
    assert _nomes(sem_gravacao) == base | {"composicao_v1.svg"}  # .ly só quando o LilyPond gravou
    gravacao = Gravacao(svgs=["<svg/>", "<svg/>"], pdfs={"grade": b"%PDF", "P1": b"%PDF"})
    completo = pacote(c, 0, lilypond={"grade": r"\score{}", "P1": r"\score{}"}, gravacao=gravacao, svg="<svg/>")
    assert _nomes(completo) == base | {"composicao_v1_grade.ly", "composicao_v1_P1.ly", "composicao_v1_grade.pdf",
                                       "composicao_v1_P1.pdf", "composicao_v1_grade-1.svg", "composicao_v1_grade-2.svg"}


def test_json_do_experimento_registra_pedido_modelo_semente_dependencias_e_diff():
    from tests.test_composicao import _quarteto
    c = _quarteto()
    exp = json.loads(experimento_json(c, motores={"LilyPond": "2.24.4"}))
    v = exp["versoes"][0]
    assert v["pedido"] == "pedido" and v["modelo"] == "m" and v["semente"] == 7
    assert v["procedimentos"] == ["serie_harmonica"] and "decisao" in v and "restricoes" in v["plano"]
    assert v["dependencias"]["abjad"] and v["dependencias"]["python"]
    assert v["diff"]["adicionados"] and v["metricas"]["notas"] > 0
    assert exp["motores"] == {"LilyPond": "2.24.4"}

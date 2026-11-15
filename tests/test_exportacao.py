import io
import json
import struct
import zipfile
from fractions import Fraction as F

from muschat.exportacao import experimento_json, metricas, pacote, para_midi
from muschat.musicir import Altura, Compasso, MusicIR, Nota, Parte, Pausa, Proveniencia, Voz
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


def _melodia(pid: str, instrumento: str, eventos: list[tuple[int | None, F] | tuple[int, F, list[str]]]) -> Parte:
    """(midicents ou None = pausa, duração[, técnicas]) em sequência, a partir do tempo 0."""
    fim, criados = F(0), []
    for k, (altura, duracao, *tecnicas) in enumerate(eventos):
        eid, prov = f"{pid}{k}", Proveniencia(origem="algoritmo")
        criados.append(Pausa(id=eid, inicio=fim, duracao=duracao, proveniencia=prov) if altura is None else
                       Nota(id=eid, inicio=fim, duracao=duracao, alturas=[Altura(midicents=altura)],
                            tecnicas=tecnicas[0] if tecnicas else [], proveniencia=prov))
        fim += duracao
    return Parte(id=pid, instrumento=instrumento, vozes=[Voz(id=f"{pid}v", eventos=criados)])


def test_metricas_de_linha_cruzamento_frase_e_tecnica():
    q = F(1, 4)
    # flauta: Dó5, Ré5 (conjunto), Ré5 (repetição), pausa, Dó4 (salto de 1400 cents), Ré4
    flauta = _melodia("fl", "Flauta", [(7200, q), (7400, q), (7400, q), (None, q),
                                       (6000, F(1, 2), ["jet whistle"]), (6200, F(1, 2), ["slap"])])
    # violoncelo acima da flauta na segunda metade do compasso 2: cruzamento
    cello = _melodia("vc", "Violoncelo", [(None, F(1)), (4800, F(1, 2)), (6500, F(1, 2))])
    ir = MusicIR(titulo="t", compassos=[Compasso(numerador=4, denominador=4)] * 2, partes=[flauta, cello])
    m = metricas(ir)
    assert m["intervalos"] == {"repeticoes": 1, "conjuntos": 2, "saltos": 2}  # 200, 0, 1400, 200 | 1700
    assert m["saltos_acima_da_oitava"] == {"fl": 1, "vc": 1}
    assert m["cruzamentos"] == 1  # Ré4 da flauta contra o Fá4 do violoncelo
    # frases separadas por pausa ou nota longa (mínima ou mais): fl = 3 ataques em 3 semínimas, depois cada mínima
    assert m["densidade_por_frase"] == {"fl": [1.0, 0.5, 0.5], "vc": [0.5, 0.5]}
    assert m["tecnicas_intocaveis"] == []  # slap no grave e jet whistle de mínima se tocam na flauta
    cello.vozes[0].eventos[1].tecnicas = ["slap"]
    assert metricas(ir)["tecnicas_intocaveis"] == ["vc c.2: slap (Violoncelo): não se toca nesta família"]


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


def test_midi_soma_notas_ligadas_num_so_ataque():
    prov = Proveniencia(origem="compositor")
    voz = Voz(id="v", eventos=[
        Nota(id="a", inicio=F(0), duracao=F(3, 4), alturas=[Altura(midicents=6000)], ligada=True, proveniencia=prov),
        Nota(id="b", inicio=F(3, 4), duracao=F(1, 2), alturas=[Altura(midicents=6000)], ligada=True,
             proveniencia=prov),
        Nota(id="c", inicio=F(5, 4), duracao=F(1, 4), alturas=[Altura(midicents=6000)], proveniencia=prov),
        Nota(id="d", inicio=F(3, 2), duracao=F(1, 2), alturas=[Altura(midicents=6200)], proveniencia=prov)])
    ir = MusicIR(titulo="t", compassos=[Compasso(numerador=4, denominador=4)] * 2,
                 partes=[Parte(id="p", instrumento="Flauta", vozes=[voz])])
    tpq, (faixa,) = _ler_midi(para_midi(ir))
    assert faixa == [(0, 60, int(F(3, 2) * 4 * tpq)), (int(F(3, 2) * 4 * tpq), 62, 2 * tpq)]


def test_tecnica_fora_do_registro_curta_demais_ou_sem_altura_precisa_e_apontada():
    clarone = _melodia("cb", "Clarinete baixo", [(7400, F(1, 4), ["slap"]), (4000, F(1, 4), ["slap"]),
                                                 (6000, F(1, 2), ["multifonico"])])
    flauta = _melodia("fl", "Flauta", [(8000, F(1, 8), ["whistle tone"]), (7250, F(1, 8), ["jet whistle"]),
                                       (8000, F(3, 4), ["whistle tone"])])
    ir = MusicIR(titulo="t", compassos=[Compasso(numerador=4, denominador=4)], partes=[flauta, clarone])
    assert metricas(ir)["tecnicas_intocaveis"] == [
        "fl c.1: whistle tone (Flauta): curta demais (mínimo 1/4)",
        "fl c.1: jet whistle (Flauta): não tem altura precisa (escrita microtonal)",
        "cb c.1: slap (Clarinete baixo): fora do registro (agudo demais)",
    ]


def test_verificador_usa_a_tessitura_propria_da_parte():
    flauta = _melodia("fl", "Flauta", [(6250, F(1), ["slap"])])
    curta = flauta.model_copy(update={"tessitura": (6100, 6300)})  # 6250: 0,75 dela, 0,07 na da tabela
    ir = MusicIR(titulo="t", compassos=[Compasso(numerador=4, denominador=4)], partes=[curta])
    assert metricas(ir)["tecnicas_intocaveis"] == ["fl c.1: slap (Flauta): fora do registro (agudo demais)"]


def test_zip_respeita_grade_transposta_sem_mudar_midi():
    import xml.etree.ElementTree as ET
    from tests.test_composicao import _quarteto

    c = _quarteto()
    c.versoes[0].ir.partes[0].instrumento = "Clarinete"
    with zipfile.ZipFile(io.BytesIO(pacote(c, 0, transposta=True))) as z:
        raiz = ET.fromstring(z.read("composicao_v1.musicxml"))
        assert raiz.findtext(".//transpose/chromatic") == "-2"
        assert z.read("composicao_v1.mid") == para_midi(c.versoes[0].ir)

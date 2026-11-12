"""Exportações da Composição: MIDI, JSON do experimento, pacote zip da Versão, métricas descritivas e o que cada formato
perde. Tudo em Python puro sobre a MusicIR: nenhum motor externo é exigido para exportar."""

import io
import json
import platform
import re
import struct
import zipfile
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from statistics import pstdev

from muschat.composicao import Composicao
from muschat.musicir import MusicIR, Nota, comparar
from muschat.notacao.lilypond import Gravacao
from muschat.notacao.musicxml import para_musicxml

TPQ = 480  # ticks por semínima
_CANAIS = [c for c in range(16) if c != 9]  # canal 10 é percussão no General MIDI
_VELOCIDADE = {"pppp": 16, "ppp": 24, "pp": 36, "p": 48, "mp": 64, "mf": 80, "f": 96, "ff": 108, "fff": 118,
               "ffff": 127, "sfz": 112, "fp": 96}

PERDAS = {
    "midi": "MIDI perde técnicas estendidas, articulações, grafia (enarmonia, quiálteras) e microtons (arredondados "
            "ao semitom); curvas, dados espectrais e eventos eletrônicos ficam de fora.",
    "musicxml": "MusicXML não leva curvas de controle, dados espectrais nem eventos eletrônicos. Edições feitas no "
                "Dorico/MuseScore não voltam ao Muschat.",
    "lilypond": "LilyPond/PDF/SVG são só gravação: não levam curvas, dados espectrais, eventos eletrônicos nem a "
                "proveniência dos eventos.",
    "json": "JSON é o formato sem perda: guarda a MusicIR completa, as Versões, a etapa pendente e a proveniência.",
}


def _vlq(n: int) -> bytes:
    saida = [n & 0x7F]
    while n := n >> 7:
        saida.append(0x80 | (n & 0x7F))
    return bytes(reversed(saida))


def _ticks(t) -> int:
    return round(t * 4 * TPQ)  # shortcut: quiálteras aninhadas que não dividem 480 arredondam ao tick


def _faixa(eventos: list[tuple[int, int, bytes]]) -> bytes:
    """(tick, ordem, mensagem) -> chunk MTrk; `ordem` põe note-off antes de note-on no mesmo tick."""
    corpo, antes = bytearray(), 0
    for tick, _, msg in sorted(eventos, key=lambda e: e[:2]):
        corpo += _vlq(tick - antes) + msg
        antes = tick
    corpo += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(corpo)) + corpo


def para_midi(ir: MusicIR) -> bytes:
    """SMF tipo 1, uma faixa por parte; andamentos e métricas na primeira. Só notas viram eventos (ver PERDAS)."""
    faixas = []
    for k, parte in enumerate(ir.partes):
        canal = _CANAIS[k % len(_CANAIS)]
        eventos: list[tuple[int, int, bytes]] = [(0, 0, b"\xff\x03" + _vlq(len(nome := parte.instrumento.encode()))
                                                  + nome)]
        if k == 0:
            for inicio, c in zip(ir.inicios(), ir.compassos):
                t = _ticks(inicio)
                eventos.append((t, 0, b"\xff\x58\x04" + bytes([c.numerador, (c.denominador.bit_length() - 1), 24, 8])))
                if c.andamento:
                    eventos.append((t, 0, b"\xff\x51\x03" + (60_000_000 // c.andamento).to_bytes(3, "big")))
        for voz in parte.vozes:
            velocidade = 80
            for e in voz.eventos:
                if not isinstance(e, Nota):
                    continue
                velocidade = _VELOCIDADE.get(e.dinamica or "", velocidade)
                for a in e.alturas:
                    nota = min((a.midicents + 50) // 100, 127)
                    eventos.append((_ticks(e.inicio), 1, bytes([0x90 | canal, nota, velocidade])))
                    eventos.append((_ticks(e.fim), 0, bytes([0x80 | canal, nota, 0])))
        faixas.append(_faixa(eventos))
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(faixas), TPQ) + b"".join(faixas)


def metricas(ir: MusicIR, *, anterior: MusicIR | None = None, violacoes: list | None = None) -> dict:
    """Descrição quantitativa da Versão (não é juízo estético). Intervalos: entre notas consecutivas de cada voz,
    pela primeira altura do acorde."""
    eventos = [e for _, _, e in ir.eventos()]
    notas = [e for e in eventos if isinstance(e, Nota)]
    alturas = [a.midicents for n in notas for a in n.alturas]
    intervalos = [abs(b.alturas[0].midicents - a.alturas[0].midicents)
                  for p in ir.partes for v in p.vozes
                  for vn in [[e for e in v.eventos if isinstance(e, Nota)]] for a, b in zip(vn, vn[1:])]
    seminimas = sum(c.comprimento for c in ir.compassos) * 4
    m = {
        "eventos": len(eventos),
        "notas": len(notas),
        "ataques_por_seminima": round(float(len(notas) / seminimas), 3) if seminimas else 0.0,
        "ambito_midicents": [min(alturas), max(alturas)] if alturas else None,
        "duracoes": {str(d): k for d, k in sorted(Counter(n.duracao for n in notas).items())},
        "desvio_intervalar_cents": round(pstdev(intervalos), 1) if intervalos else None,
    }
    if violacoes is not None:
        m["violacoes"] = len(violacoes)
    if anterior is not None:
        d = comparar(anterior, ir)
        m["diff"] = {"adicionados": len(d.adicionados), "removidos": len(d.removidos), "alterados": len(d.alterados)}
    return m


def dependencias() -> dict[str, str | None]:
    """Versões das dependências Python que produzem e escrevem a música (vão no experimento de cada Versão)."""
    def v(pacote: str) -> str | None:
        try:
            return version(pacote)
        except PackageNotFoundError:
            return None

    return {"python": platform.python_version(), **{p: v(p) for p in ("muschat", "abjad", "verovio", "google-genai")}}


def experimento_json(c: Composicao, *, motores: dict[str, str] | None = None) -> str:
    """Dados de pesquisa de todas as Versões: pedido, modelo, plano (restrições), decisão do roteador, procedimentos,
    parâmetros, semente, dependências, violações, diff com a Versão anterior e métricas. `motores`: versões dos
    motores externos detectados (entram só quando presentes)."""
    versoes = []
    for i, v in enumerate(c.versoes):
        anterior = c.versoes[v.anterior].ir if v.anterior is not None else None
        m = metricas(v.ir, anterior=anterior, violacoes=v.experimento.get("violacoes", []))
        diff = m.pop("diff", None) or {"adicionados": m["eventos"], "removidos": 0, "alterados": 0}
        versoes.append({"versao": i + 1, "anterior": None if v.anterior is None else v.anterior + 1,
                        "data": v.data.isoformat(), "resumo": v.resumo,
                        **{k: x for k, x in v.experimento.items() if k != "intermediarios"},
                        "diff": diff, "metricas": m})
    dados = {"versoes": versoes, "pendente": c.pendente.model_dump(mode="json") if c.pendente else None}
    if motores:
        dados["motores"] = motores
    return json.dumps(dados, ensure_ascii=False, indent=2)


def _seguro(texto: str) -> str:
    return re.sub(r"[^\w-]+", "_", texto).strip("_") or "parte"


def pacote(c: Composicao, indice: int, *, lilypond: dict[str, str] | None = None, gravacao: Gravacao | None = None,
           svg: str | None = None, motores: dict[str, str] | None = None) -> bytes:
    """Zip da Versão `indice` só com o que foi produzido: MusicXML, MIDI e JSONs sempre; `.ly` se o Abjad escreveu;
    PDF/SVG do LilyPond se ele gravou, senão o SVG do Verovio se houver."""
    ir, nome = c.versoes[indice].ir, f"composicao_v{indice + 1}"
    arquivos: dict[str, str | bytes] = {f"{nome}.musicxml": para_musicxml(ir), f"{nome}.mid": para_midi(ir),
                                        "composicao.json": c.para_json(),
                                        "experimento.json": experimento_json(c, motores=motores)}
    formatos = ["musicxml", "midi", "json"]
    for fonte, texto in (lilypond or {}).items():
        arquivos[f"{nome}_{_seguro(fonte)}.ly"] = texto
    if gravacao and gravacao.pdfs:
        arquivos |= {f"{nome}_{_seguro(fonte)}.pdf": pdf for fonte, pdf in gravacao.pdfs.items()}
        arquivos |= {f"{nome}_grade-{k}.svg": s for k, s in enumerate(gravacao.svgs, 1)}
    elif svg:
        arquivos[f"{nome}.svg"] = svg
    if lilypond or (gravacao and gravacao.pdfs):
        formatos.insert(2, "lilypond")
    arquivos["PERDAS.txt"] = "\n\n".join(f"{f.upper()}: {PERDAS[f]}" for f in formatos) + "\n"
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho, conteudo in arquivos.items():
            z.writestr(caminho, conteudo)
    return saida.getvalue()

"""Peças de referência: os mesmos pedidos, com plano e semente fixos (sem LLM), gerados pelo motor da vez e guardados
lado a lado para o compositor comparar (escuta, leitura, métricas descritivas). Nunca é nota de qualidade.

    uv run python -m muschat.referencias linha-de-base

Saída em .muschat/referencias/<rótulo>/ (ignorada pelo git): por peça, MusicXML, JSON da composição e PDFs (quando o
LilyPond estiver instalado); para o conjunto, metricas.json e checklist.md para preencher.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from muschat.composicao import Composicao
from muschat.exportacao import metricas
from muschat.notacao.lilypond import detectar, gravar, para_lilypond
from muschat.notacao.musicxml import para_musicxml
from muschat.orquestrador import compor

PASTA = Path(".muschat") / "referencias"


def _op(procedimento: str, **parametros: float) -> dict:
    return {"procedimento": procedimento, "parametros": [{"nome": k, "valor": v} for k, v in parametros.items()]}


# (nome, pedido como o compositor escreveria, plano fixo). Mudar uma peça invalida a comparação com rótulos antigos.
PECAS: list[tuple[str, str, dict]] = [
    ("quarteto-espectral",
     "Oito compassos para quarteto de cordas sobre os parciais de Dó 2, com sul ponticello e harmônicos, crescendo.",
     {"instrumentos": ["violino", "violino", "viola", "violoncelo"], "tecnicas": ["sul ponticello", "harmonico"],
      "operacoes": [_op("serie_harmonica", fundamental=3600, parciais=16, compassos=8, microtom=1),
                    _op("aplicar_tecnicas", proporcao=0.3), _op("dinamica_progressiva", inicial=2, final=7)],
      "semente": 1}),
    ("flauta-clarone",
     "Série harmônica sobre 3600 mc para flauta e clarinete baixo, com frullato, slap, jet whistle e multifônicos.",
     {"instrumentos": ["flauta", "clarinete baixo"], "tecnicas": ["frullato", "slap", "jet whistle", "multifonico"],
      "operacoes": [_op("serie_harmonica", fundamental=3600, parciais=16, compassos=8, microtom=1),
                    _op("aplicar_tecnicas", proporcao=0.4)],
      "semente": 2}),
    ("misto-adensando",
     "Oito compassos para flauta, clarinete, violino e violoncelo sobre o espectro de Sol 2, adensando do início ao "
     "fim, com pizzicato, staccato e frullato.",
     {"instrumentos": ["flauta", "clarinete", "violino", "violoncelo"],
      "tecnicas": ["pizzicato", "staccato", "frullato"],
      "operacoes": [_op("serie_harmonica", fundamental=4300, parciais=12, compassos=8, microtom=1),
                    _op("densidade", inicial=1, final=3), _op("aplicar_tecnicas", proporcao=0.3)],
      "semente": 3}),
    ("trio-subindo",
     "Oito compassos para trio de cordas sobre os parciais de Sol 1, subindo de registro e rarefazendo, com col legno "
     "battuto e sul tasto.",
     {"instrumentos": ["violino", "viola", "violoncelo"], "tecnicas": ["col legno battuto", "sul tasto"],
      "operacoes": [_op("serie_harmonica", fundamental=3100, parciais=16, compassos=8, microtom=1),
                    _op("registro_progressivo", inicial=0, final=1200), _op("densidade", inicial=2, final=0.5),
                    _op("aplicar_tecnicas", proporcao=0.3)],
      "semente": 4}),
]

CHECKLIST = """\
- [ ] Partes transpostas corretas (cada intérprete lê a sua parte)
- [ ] Nada intocável (registro, técnica, duração)
- [ ] Frases identificáveis (começo, chegada, respiração)
- [ ] Saltos justificados (nada de oitavas soltas sem motivo)
- [ ] Técnica onde soa (no registro e no gesto em que funciona)
- Escuta (1 a 5, comentário):
- Leitura (1 a 5, comentário):
"""


class _PlanoFixo:
    """Gerador que devolve sempre o plano fixo: o caminho é o do chat, sem chamar o modelo."""

    def __init__(self, plano: dict) -> None:
        self.texto = json.dumps(plano, ensure_ascii=False)

    def generate_content(self, *, model, contents, config):  # noqa: ARG002  (mesma assinatura do SDK)
        return SimpleNamespace(text=self.texto)


class _SemHistorico:
    def get_history(self) -> list:
        return []


def gerar(rotulo: str, *, raiz: Path = PASTA, lilypond=None) -> Path:
    """Gera todas as peças sob raiz/rotulo; levanta RuntimeError se alguma for recusada (a linha de base precisa
    estar completa para valer como régua)."""
    pasta = raiz / rotulo
    pasta.mkdir(parents=True, exist_ok=True)
    todas, checklist = {}, [f"# Checklist de leitura: {rotulo}\n"]
    for nome, pedido, plano in PECAS:
        composicao = Composicao()
        resultado = compor(pedido, composicao, gerador=_PlanoFixo({"objetivo": pedido, **plano}),
                           chat=_SemHistorico(), modelo="plano-fixo-sem-LLM")
        if resultado.versao is None:
            raise RuntimeError(f"{nome}: {resultado.resumo}")
        ir = resultado.versao.ir
        (pasta / f"{nome}.musicxml").write_text(para_musicxml(ir), encoding="utf-8")
        (pasta / f"{nome}.json").write_text(composicao.para_json(), encoding="utf-8")
        for chave, pdf in gravar(para_lilypond(ir), lilypond).pdfs.items():
            (pasta / f"{nome}-{chave}.pdf").write_bytes(pdf)
        todas[nome] = {"pedido": pedido, "resumo": resultado.versao.resumo, **metricas(ir)}
        checklist.append(f"## {nome}\n\n{pedido}\n\n{CHECKLIST}")
    (pasta / "metricas.json").write_text(json.dumps(todas, ensure_ascii=False, indent=2), encoding="utf-8")
    (pasta / "checklist.md").write_text("\n".join(checklist), encoding="utf-8")
    return pasta


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: uv run python -m muschat.referencias <rótulo>   (ex.: linha-de-base)")
    print(gerar(sys.argv[1], lilypond=detectar()).resolve())

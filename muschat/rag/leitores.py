"""Leitores do corpus: cada tipo de arquivo vira chunks que são unidades de sentido."""

import copy
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from pypdf import PdfReader

MAX_SECAO = 2000
JANELA, SOBREPOSICAO = 1000, 200
COMPASSOS_POR_FICHA, PASSO_FICHA = 3, 2
MIN_ROTULOS_DICIONARIO = 3
METADADOS = "_acervo.toml"  # por pasta: compositor = "..." (vale para a pasta e subpastas)

FAMILIAS = {
    "cordas": ("cordas", "archi", "violin", "viola", "violoncel", "contrabbass", "contrabaixo", "arpa"),
    "sopros": ("sopros", "legni", "corno ingl", "english horn", "flaut", "clarinet", "oboe", "fagot", "saxofon", "saxophon"),
    "metais": ("metais", "ottoni", "tromba", "trompete", "corno", "trombone", "tuba"),
    "piano": ("piano",),
    "percussao": ("percuss",),
    "voz": ("voz", "voce", "vocal", "soprano", "mezzo", "baritono"),
    "eletroacustica": ("eletroac", "elettronic", "electronic", "acusmat", "live electr"),
}


@dataclass(frozen=True)
class Chunk:
    id: str
    texto: str
    fonte: str
    arquivo: str
    local: str  # "p. 12" | "m.7–9" | "obra" | trilha de cabeçalhos | rótulo do exemplo
    tipo: str  # texto | analise_obra | livro | partitura | exemplo_notado
    familia: str = ""
    obra: str | None = None
    compositor: str = ""
    extra: dict = field(default_factory=dict)  # "musicxml": documento autônomo p/ renderizar o trecho

    @property
    def texto_embutido(self) -> str:
        """Cabeçalho de contexto determinístico + texto (o que vai para o embedding)."""
        cabecalho = " | ".join(p for p in (self.fonte, self.local, self.familia, self.compositor) if p)
        return f"{cabecalho}\n{self.texto}"


def ler(arquivo: Path, raiz: Path) -> list[Chunk]:
    funcao = _LEITORES.get(arquivo.suffix.lower())
    if funcao is None:
        return []
    relativo = arquivo.relative_to(raiz)
    rel = relativo.as_posix()
    pasta = familia_de(*relativo.parent.parts)
    compositor = _compositor(arquivo.parent, raiz)
    return [
        Chunk(id=f"{rel}#{i}", arquivo=rel, compositor=compositor, **campos)
        for i, campos in enumerate(funcao(arquivo, pasta))
    ]


def _compositor(pasta: Path, raiz: Path) -> str:
    """Autor declarado no _acervo.toml mais próximo (da pasta do arquivo até a raiz do corpus)."""
    for atual in (pasta, *pasta.parents):
        meta = atual / METADADOS
        if meta.exists():
            return str(tomllib.loads(meta.read_text(encoding="utf-8")).get("compositor", "")).strip()
        if atual == raiz:
            break
    return ""


def familia_de(*textos: str) -> str:
    """Família instrumental (cordas, sopros, ...) por palavra-chave; "" se nenhuma."""
    alvo = " ".join(textos).lower()
    for familia, chaves in FAMILIAS.items():
        if any(chave in alvo for chave in chaves):
            return familia
    return ""


def _familia_de_partes(nomes: list[str], padrao: str) -> str:
    familias = {f for f in (familia_de(n) for n in nomes) if f}
    if len(familias) == 1:
        return familias.pop()
    return "misto" if familias else padrao


def _janelas(texto: str, tamanho: int = JANELA, sobreposicao: int = SOBREPOSICAO) -> list[str]:
    partes, inicio = [], 0
    while inicio < len(texto):
        partes.append(texto[inicio : inicio + tamanho])
        if inicio + tamanho >= len(texto):
            break
        inicio += tamanho - sobreposicao
    return partes


# --- Markdown: um chunk por seção de cabeçalho -------------------------------

_CABECALHO = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _ler_md(arq: Path, pasta: str) -> Iterator[dict[str, Any]]:
    linhas = arq.read_text(encoding="utf-8").splitlines()
    partitura = arq.with_suffix(".musicxml")
    tem_partitura = partitura.exists()
    obra = arq.stem if tem_partitura else None
    tipo = "analise_obra" if tem_partitura else "texto"
    pasta = pasta or (_familia_de_partes(_nomes_das_partes(ET.parse(partitura).getroot()), "") if tem_partitura else "")

    secoes: list[tuple[list[str], list[str]]] = [([], [])]  # (trilha, linhas do corpo)
    trilha: list[tuple[int, str]] = []
    em_codigo = False
    for linha in linhas:
        if linha.lstrip().startswith("```"):
            em_codigo = not em_codigo
        m = None if em_codigo else _CABECALHO.match(linha)
        if m:
            nivel = len(m.group(1))
            trilha = [(n, t) for n, t in trilha if n < nivel] + [(nivel, m.group(2))]
            secoes.append(([t for _, t in trilha], []))
        else:
            secoes[-1][1].append(linha)

    titulo_h1 = next((caminho[0] for caminho, _ in secoes if caminho), None)
    fonte = titulo_h1 or arq.stem
    for caminho, corpo in secoes:
        texto = "\n".join(corpo).strip()
        if not texto:
            continue
        local = " > ".join(caminho)
        familia = pasta or next((f for t in reversed(caminho) if (f := familia_de(t))), "")
        for pedaco in _quebrar_secao(texto):
            yield dict(texto=pedaco, fonte=fonte, local=local, tipo=tipo, familia=familia, obra=obra)


def _quebrar_secao(texto: str) -> list[str]:
    if len(texto) <= MAX_SECAO:
        return [texto]
    pedacos, atual = [], ""
    for paragrafo in texto.split("\n\n"):
        for parte in _janelas(paragrafo, MAX_SECAO, SOBREPOSICAO) if len(paragrafo) > MAX_SECAO else [paragrafo]:
            if atual and len(atual) + 2 + len(parte) > MAX_SECAO:
                pedacos.append(atual)
                atual = parte
            else:
                atual = f"{atual}\n\n{parte}" if atual else parte
    return pedacos + [atual] if atual else pedacos


# --- PDF / TXT: por página, em janelas ---------------------------------------

def _ler_pdf(arq: Path, pasta: str) -> Iterator[dict[str, Any]]:
    for n, pagina in enumerate(PdfReader(arq).pages, 1):
        for pedaco in _janelas((pagina.extract_text() or "").strip()):
            yield dict(texto=pedaco, fonte=arq.stem, local=f"p. {n}", tipo="livro", familia=pasta)


def _ler_txt(arq: Path, pasta: str) -> Iterator[dict[str, Any]]:
    for pedaco in _janelas(arq.read_text(encoding="utf-8").strip()):
        yield dict(texto=pedaco, fonte=arq.stem, local="", tipo="livro", familia=pasta)


# --- MusicXML: ficha da obra + fichas de compasso, ou dicionário numerado -----

_ROTULO = re.compile(r"^\s*\d+\.\s+\S")
_ALTER = {"1": "#", "-1": "b", "0.5": "+¼", "-0.5": "-¼", "2": "##", "-2": "bb"}


def _ler_musicxml(arq: Path, pasta: str) -> Iterator[dict[str, Any]]:
    raiz = ET.parse(arq).getroot()
    titulo = raiz.findtext("work/work-title") or raiz.findtext("movement-title") or arq.stem
    nomes = dict(zip((sp.get("id") for sp in raiz.iter("score-part")), _nomes_das_partes(raiz)))
    partes = [(nomes.get(p.get("id"), "?"), p.findall("measure")) for p in raiz.findall("part")]
    rotulado = sum(bool(_ROTULO.match(w.text or "")) for w in raiz.iter("words")) >= MIN_ROTULOS_DICIONARIO
    if rotulado:
        yield from _exemplos_numerados(partes, titulo, pasta, arq.stem)
    else:
        yield from _fichas_partitura(raiz, partes, titulo, pasta, arq.stem)


def _nomes_das_partes(raiz: ET.Element) -> list[str]:
    return [sp.findtext("part-name") or sp.get("id") or "?" for sp in raiz.iter("score-part")]


def _descrever(compasso: ET.Element) -> str:
    itens = [w.text.strip() for w in compasso.iter("words") if w.text and w.text.strip()]
    itens += [f"notehead {h}" for h in _unicos(n.text for n in compasso.iter("notehead")) if h != "normal"]
    for tecnico in compasso.iter("technical"):
        itens += [filho.tag for filho in tecnico]
    if any(True for _ in compasso.iter("tremolo")):
        itens.append("tremolo")
    if any(True for _ in compasso.iter("glissando")) or any(True for _ in compasso.iter("slide")):
        itens.append("glissando")
    itens += [d.tag for dinamica in compasso.iter("dynamics") for d in dinamica]
    alturas = _unicos(_altura(n) for n in compasso.iter("note") if n.find("rest") is None)
    if alturas:
        itens.append("alturas " + " ".join(alturas[:8]))
    return "; ".join(_unicos(itens))


def _altura(nota: ET.Element) -> str:
    p = nota.find("pitch")
    if p is None:
        return "s/altura"
    return f"{p.findtext('step')}{_ALTER.get(p.findtext('alter') or '', '')}{p.findtext('octave')}"


def _unicos(itens) -> list[str]:
    return list(dict.fromkeys(i for i in itens if i))


def _fichas_partitura(raiz, partes, titulo, pasta, obra) -> Iterator[dict[str, Any]]:
    familia = _familia_de_partes([n for n, _ in partes], pasta)
    total = max((len(ms) for _, ms in partes), default=0)
    tempo = next((m.text for m in raiz.iter("per-minute")), None) or next(
        (s.get("tempo") for s in raiz.iter("sound") if s.get("tempo")), None
    )
    metrica = next((f"{t.findtext('beats')}/{t.findtext('beat-type')}" for t in raiz.iter("time")), None)
    ficha = [f"Obra: {titulo}", f"Instrumentação: {', '.join(n for n, _ in partes)}", f"Extensão: {total} compassos"]
    ficha += [f"Andamento: ♩={tempo}"] * bool(tempo) + [f"Métrica: {metrica}"] * bool(metrica)
    base = dict(fonte=titulo, tipo="partitura", familia=familia, obra=obra)
    yield dict(texto="\n".join(ficha), local="obra", **base)

    for inicio in range(0, max(total - 1, 1), PASSO_FICHA):
        linhas = [
            f"{nome}, compasso {c.get('number')}: {desc}"
            for nome, compassos in partes
            for c in compassos[inicio : inicio + COMPASSOS_POR_FICHA]
            if (desc := _descrever(c))
        ]
        janela = max((ms for _, ms in partes), key=len)[inicio : inicio + COMPASSOS_POR_FICHA]
        if linhas and janela:
            local = f"m.{janela[0].get('number')}–{janela[-1].get('number')}"
            trecho = [(nome, compassos[inicio : inicio + COMPASSOS_POR_FICHA], compassos) for nome, compassos in partes]
            yield dict(texto="\n".join(linhas), local=local, extra={"musicxml": _documento(titulo, trecho)}, **base)


def _exemplos_numerados(partes, titulo, pasta, obra) -> Iterator[dict[str, Any]]:
    exemplos = []
    for nome, compassos in partes:
        for c in compassos:
            rotulo = next((w.text.strip() for w in c.iter("words") if _ROTULO.match(w.text or "")), None)
            if rotulo is None:
                continue
            exemplos.append((int(rotulo.split(".")[0]), dict(
                texto=f"{titulo} — {rotulo}\n{nome}, compasso {c.get('number')}: {_descrever(c)}",
                fonte=titulo, local=rotulo, tipo="exemplo_notado", familia=familia_de(nome) or pasta, obra=obra,
                extra={"parte": nome, "musicxml": _documento(f"{titulo} — {rotulo}", [(nome, [c], compassos)])},
            )))
    yield from (campos for _, campos in sorted(exemplos, key=lambda e: e[0]))


def _documento(titulo: str, partes: list[tuple[str, list[ET.Element], list[ET.Element]]]) -> str:
    """score-partwise autônomo com só os compassos pedidos de cada parte (nome, trecho, parte inteira)."""
    raiz = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(raiz, "work"), "work-title").text = titulo
    lista = ET.SubElement(raiz, "part-list")
    for i, (nome, trecho, inteira) in enumerate(partes, 1):
        ET.SubElement(ET.SubElement(lista, "score-part", id=f"P{i}"), "part-name").text = nome
        parte = ET.SubElement(raiz, "part", id=f"P{i}")
        for n, compasso in enumerate(trecho):
            copia = copy.deepcopy(compasso)
            if n == 0:
                _herdar_atributos(copia, inteira)
            parte.append(copia)
    return ET.tostring(raiz, encoding="unicode")


def _herdar_atributos(compasso: ET.Element, parte: list[ET.Element]) -> None:
    """Completa o 1º compasso do trecho com divisions/key/time/clef da parte (o que ele já tiver vence)."""
    # shortcut: herda dos primeiros atributos da parte; uma mudança de clave/métrica no meio da peça,
    # antes do trecho, não é aplicada (acumular os <attributes> até o trecho se isso aparecer)
    base = next((c.find("attributes") for c in parte if c.find("attributes") is not None), None)
    if base is None:
        return
    proprios = compasso.find("attributes")
    if proprios is None:
        compasso.insert(0, copy.deepcopy(base))
        return
    ordem = ["divisions", "key", "time", "staves", "clef"]
    for tag in ordem:
        if proprios.find(tag) is None and (herdado := base.find(tag)) is not None:
            proprios.append(copy.deepcopy(herdado))
    proprios[:] = sorted(proprios, key=lambda e: ordem.index(e.tag) if e.tag in ordem else len(ordem))


_LEITORES = {".md": _ler_md, ".pdf": _ler_pdf, ".txt": _ler_txt, ".musicxml": _ler_musicxml}
EXTENSOES = tuple(_LEITORES)

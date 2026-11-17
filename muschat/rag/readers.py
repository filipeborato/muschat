"""Corpus readers: each supported file format is split into semantically coherent chunks."""

import copy
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from pypdf import PdfReader
from muschat.instruments import identify, INSTRUMENTS
import unicodedata

MAX_SECTION_CHARS = 2000
WINDOW_SIZE, WINDOW_OVERLAP = 1000, 200
MEASURES_PER_CARD, CARD_STEP = 3, 2
MIN_DICTIONARY_LABELS = 3
METADATA_FILES = ("_corpus.toml", "_acervo.toml")  # per folder: composer = "..." (applies to folder and subfolders)

FAMILIES = {
    "strings": (
        "strings",
        "cordas",
        "archi",
        "violin",
        "viola",
        "violoncel",
        "cello",
        "contrabbass",
        "contrabaixo",
        "double bass",
        "arpa",
        "harp",
    ),
    "woodwinds": (
        "woodwinds",
        "sopros",
        "legni",
        "corno ingl",
        "english horn",
        "flaut",
        "flut",
        "clarinet",
        "oboe",
        "fagot",
        "bassoon",
        "saxofon",
        "saxophon",
    ),
    "brass": ("brass", "metais", "ottoni", "tromba", "trompete", "trumpet", "corno", "horn", "trombone", "tuba"),
    "keyboard": ("keyboard", "teclado", "piano"),
    "harp": ("harp", "harpa", "arpa"),
    "guitar": ("guitar", "violao", "guitarra"),
    "orchestra": ("orchestra", "orquestra"),
    "chamber": ("chamber", "camara"),
    "percussion": ("percuss",),
    "voice": ("voice", "voz", "voce", "vocal", "soprano", "mezzo", "baritono", "baritone"),
    "electroacoustic": ("electroac", "eletroac", "elettronic", "electronic", "acusmat", "acousmat", "live electr"),
}


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source: str
    file: str
    location: str  # "p. 12" | "m.7–9" | "work" | heading trail | example label
    kind: str  # text | work_analysis | book | score | notated_example
    family: str = ""
    work: str | None = None
    composer: str = ""
    extra: dict = field(default_factory=dict)  # "musicxml": standalone document for rendering the excerpt

    @property
    def embedded_text(self) -> str:
        """Deterministic context header + text (the string passed to the embedder)."""
        header = " | ".join(p for p in (self.source, self.location, self.family, self.composer) if p)
        return f"{header}\n{self.text}"


def read_file(path: Path, root: Path) -> list[Chunk]:
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        return []
    relative = path.relative_to(root)
    rel = relative.as_posix()
    folder_family = family_of(*relative.parent.parts)
    composer = _composer(path.parent, root)
    return [
        Chunk(id=f"{rel}#{i}", file=rel, composer=composer, **fields)
        for i, fields in enumerate(reader(path, folder_family))
    ]


def _composer(folder: Path, root: Path) -> str:
    """Composer declared in the nearest `_corpus.toml` or `_acervo.toml` up to the corpus root."""
    for current in (folder, *folder.parents):
        for filename in METADATA_FILES:
            meta = current / filename
            if meta.exists():
                data = tomllib.loads(meta.read_text(encoding="utf-8"))
                return str(data.get("composer") or data.get("compositor") or "").strip()
        if current == root:
            break
    return ""


def family_of(*texts: str) -> str:
    """Instrumental family (`strings`, `woodwinds`, ...) by keyword; `""` if none matches."""
    target = unicodedata.normalize("NFKD", " ".join(texts).lower()).encode("ascii", "ignore").decode()
    try:
        return INSTRUMENTS[identify(target)].family
    except ValueError:
        pass
    for family, keywords in FAMILIES.items():
        if any(kw in target for kw in keywords):
            return family
    return ""


def _family_of_parts(names: list[str], default: str) -> str:
    families = {f for f in (family_of(n) for n in nomes) if f} if (nomes := names) else set()
    if len(families) == 1:
        return families.pop()
    return "mixed" if families else default


def _windows(text: str, size: int = WINDOW_SIZE, overlap: int = WINDOW_OVERLAP) -> list[str]:
    slices, start = [], 0
    while start < len(text):
        slices.append(text[start : start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return slices


# --- Markdown: one chunk per heading section ---------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _read_md(path: Path, folder_family: str) -> Iterator[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    sibling_score = path.with_suffix(".musicxml")
    has_score = sibling_score.exists()
    work = path.stem if has_score else None
    kind = "work_analysis" if has_score else "text"
    folder_family = folder_family or (
        _family_of_parts(_part_names(ET.parse(sibling_score).getroot()), "") if has_score else ""
    )

    sections: list[tuple[list[str], list[str]]] = [([], [])]
    trail: list[tuple[int, str]] = []
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            level = len(m.group(1))
            trail = [(n, t) for n, t in trail if n < level] + [(level, m.group(2))]
            sections.append(([t for _, t in trail], []))
        else:
            sections[-1][1].append(line)

    h1_title = next((heading_path[0] for heading_path, _ in sections if heading_path), None)
    source = h1_title or path.stem
    for heading_path, body in sections:
        text = "\n".join(body).strip()
        if not text:
            continue
        location = " > ".join(heading_path)
        family = folder_family or next((f for t in reversed(heading_path) if (f := family_of(t))), "")
        for piece in _split_section(text):
            yield dict(text=piece, source=source, location=location, kind=kind, family=family, work=work)


def _split_section(text: str) -> list[str]:
    if len(text) <= MAX_SECTION_CHARS:
        return [text]
    pieces, current = [], ""
    for paragraph in text.split("\n\n"):
        parts = (
            _windows(paragraph, MAX_SECTION_CHARS, WINDOW_OVERLAP)
            if len(paragraph) > MAX_SECTION_CHARS
            else [paragraph]
        )
        for part in parts:
            if current and len(current) + 2 + len(part) > MAX_SECTION_CHARS:
                pieces.append(current)
                current = part
            else:
                current = f"{current}\n\n{part}" if current else part
    return pieces + [current] if current else pieces


# --- PDF / TXT: per page, windowed -------------------------------------------


def _read_pdf(path: Path, folder_family: str) -> Iterator[dict[str, Any]]:
    for n, page in enumerate(PdfReader(path).pages, 1):
        for piece in _windows((page.extract_text() or "").strip()):
            yield dict(text=piece, source=path.stem, location=f"p. {n}", kind="book", family=folder_family)


def _read_txt(path: Path, folder_family: str) -> Iterator[dict[str, Any]]:
    for piece in _windows(path.read_text(encoding="utf-8").strip()):
        yield dict(text=piece, source=path.stem, location="", kind="book", family=folder_family)


# --- MusicXML: work card + measure cards, or numbered technique dictionary ---

_LABEL = re.compile(r"^\s*\d+\.\s+\S")
_ALTER = {"1": "#", "-1": "b", "0.5": "+¼", "-0.5": "-¼", "2": "##", "-2": "bb"}


def _read_musicxml(path: Path, folder_family: str) -> Iterator[dict[str, Any]]:
    root = ET.parse(path).getroot()
    title = root.findtext("work/work-title") or root.findtext("movement-title") or path.stem
    names = dict(zip((sp.get("id") for sp in root.iter("score-part")), _part_names(root)))
    parts = [(names.get(p.get("id"), "?"), p.findall("measure")) for p in root.findall("part")]
    is_dictionary = sum(bool(_LABEL.match(w.text or "")) for w in root.iter("words")) >= MIN_DICTIONARY_LABELS
    if is_dictionary:
        yield from _numbered_examples(parts, title, folder_family, path.stem)
    else:
        yield from _score_cards(root, parts, title, folder_family, path.stem)


def _part_names(root: ET.Element) -> list[str]:
    return [sp.findtext("part-name") or sp.get("id") or "?" for sp in root.iter("score-part")]


def _describe_measure(measure: ET.Element) -> str:
    items = [w.text.strip() for w in measure.iter("words") if w.text and w.text.strip()]
    items += [f"notehead {h}" for h in _unique(n.text for n in measure.iter("notehead")) if h != "normal"]
    for technical in measure.iter("technical"):
        items += [child.tag for child in technical]
    if any(True for _ in measure.iter("tremolo")):
        items.append("tremolo")
    if any(True for _ in measure.iter("glissando")) or any(True for _ in measure.iter("slide")):
        items.append("glissando")
    items += [d.tag for dyn in measure.iter("dynamics") for d in dyn]
    pitches = _unique(_pitch_label(n) for n in measure.iter("note") if n.find("rest") is None)
    if pitches:
        items.append("pitches " + " ".join(pitches[:8]))
    return "; ".join(_unique(items))


def _pitch_label(note: ET.Element) -> str:
    p = note.find("pitch")
    if p is None:
        return "unpitched"
    return f"{p.findtext('step')}{_ALTER.get(p.findtext('alter') or '', '')}{p.findtext('octave')}"


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _score_cards(root, parts, title, folder_family, work) -> Iterator[dict[str, Any]]:
    family = _family_of_parts([n for n, _ in parts], folder_family)
    total = max((len(ms) for _, ms in parts), default=0)
    tempo = next((m.text for m in root.iter("per-minute")), None) or next(
        (s.get("tempo") for s in root.iter("sound") if s.get("tempo")), None
    )
    meter = next((f"{t.findtext('beats')}/{t.findtext('beat-type')}" for t in root.iter("time")), None)
    card = [
        f"Work: {title}",
        f"Instrumentation: {', '.join(n for n, _ in parts)}",
        f"Length: {total} measures",
    ]
    card += [f"Tempo: ♩={tempo}"] * bool(tempo) + [f"Meter: {meter}"] * bool(meter)
    base = dict(source=title, kind="score", family=family, work=work)
    yield dict(text="\n".join(card), location="work", **base)

    for start in range(0, max(total - 1, 1), CARD_STEP):
        lines = [
            f"{name}, measure {m.get('number')}: {desc}"
            for name, measures in parts
            for m in measures[start : start + MEASURES_PER_CARD]
            if (desc := _describe_measure(m))
        ]
        window = max((ms for _, ms in parts), key=len)[start : start + MEASURES_PER_CARD]
        if lines and window:
            location = f"m.{window[0].get('number')}–{window[-1].get('number')}"
            excerpt = [(name, measures[start : start + MEASURES_PER_CARD], measures) for name, measures in parts]
            yield dict(
                text="\n".join(lines),
                location=location,
                extra={"musicxml": _standalone_document(title, excerpt)},
                **base,
            )


def _numbered_examples(parts, title, folder_family, work) -> Iterator[dict[str, Any]]:
    examples = []
    for name, measures in parts:
        for m in measures:
            label = next((w.text.strip() for w in m.iter("words") if _LABEL.match(w.text or "")), None)
            if label is None:
                continue
            examples.append(
                (
                    int(label.split(".")[0]),
                    dict(
                        text=f"{title} — {label}\n{name}, measure {m.get('number')}: {_describe_measure(m)}",
                        source=title,
                        location=label,
                        kind="notated_example",
                        family=family_of(name) or folder_family,
                        work=work,
                        extra={
                            "part": name,
                            "musicxml": _standalone_document(f"{title} — {label}", [(name, [m], measures)]),
                        },
                    ),
                )
            )
    yield from (fields for _, fields in sorted(examples, key=lambda e: e[0]))


def _standalone_document(title: str, parts: list[tuple[str, list[ET.Element], list[ET.Element]]]) -> str:
    """Standalone `score-partwise` containing only the requested measures of each part."""
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = title
    part_list = ET.SubElement(root, "part-list")
    for i, (name, excerpt, full_part) in enumerate(parts, 1):
        ET.SubElement(ET.SubElement(part_list, "score-part", id=f"P{i}"), "part-name").text = name
        part_el = ET.SubElement(root, "part", id=f"P{i}")
        for n, measure in enumerate(excerpt):
            copied = copy.deepcopy(measure)
            if n == 0:
                _inherit_attributes(copied, full_part)
            part_el.append(copied)
    return ET.tostring(root, encoding="unicode")


def _inherit_attributes(measure: ET.Element, full_part: list[ET.Element]) -> None:
    """Fill in the first measure of an excerpt with divisions/key/time/clef from the part."""
    base = next((m.find("attributes") for m in full_part if m.find("attributes") is not None), None)
    if base is None:
        return
    own = measure.find("attributes")
    if own is None:
        measure.insert(0, copy.deepcopy(base))
        return
    order = ["divisions", "key", "time", "staves", "clef"]
    for tag in order:
        if own.find(tag) is None and (inherited := base.find(tag)) is not None:
            own.append(copy.deepcopy(inherited))
    own[:] = sorted(own, key=lambda e: order.index(e.tag) if e.tag in order else len(order))


_READERS = {".md": _read_md, ".pdf": _read_pdf, ".txt": _read_txt, ".musicxml": _read_musicxml}
EXTENSIONS = tuple(_READERS)

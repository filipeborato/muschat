from pathlib import Path

from muschat.rag.readers import read_file

GRIMOIRE = """\
# Grimório

Intro geral.

## 1. ARCHI

### 1.1 Posição do arco

#### Sul ponticello estremo (s.p.e.)
- **Gráfica:** "sul pont. estr."
- **Uso:** Sciarrino.

#### Sul tasto (s.t.)
- **Efeito:** flautando.

```xml
# this is not a heading
```

## 2. LEGNI

#### Frullato (flz.)
- **Gráfica:** "flz."
"""


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        file_path.write_bytes(content)
    else:
        file_path.write_text(content, encoding="utf-8")
    return file_path


def test_markdown_splits_into_one_chunk_per_heading_with_path(tmp_path):
    chunks = read_file(_write(tmp_path, "grimorio.md", GRIMOIRE), tmp_path)
    locations = [c.location for c in chunks]
    assert locations == [
        "Grimório",
        "Grimório > 1. ARCHI > 1.1 Posição do arco > Sul ponticello estremo (s.p.e.)",
        "Grimório > 1. ARCHI > 1.1 Posição do arco > Sul tasto (s.t.)",
        "Grimório > 2. LEGNI > Frullato (flz.)",
    ]
    spe = chunks[1]
    assert "Sciarrino" in spe.text and "Sul tasto" not in spe.text
    assert "# this is not a heading" in chunks[2].text
    assert spe.source == "Grimório"
    assert spe.kind == "text"


def test_family_comes_from_heading_path_or_folder(tmp_path):
    chunks = read_file(_write(tmp_path, "grimorio.md", GRIMOIRE), tmp_path)
    assert [c.family for c in chunks] == ["", "strings", "strings", "woodwinds"]
    other = read_file(_write(tmp_path, "cordas/notes.md", "# Notes\n\ntext"), tmp_path)
    assert other[0].family == "strings"


def test_long_section_splits_by_paragraph_keeping_path(tmp_path):
    body = "\n\n".join(f"paragraph {i} " + "x" * 900 for i in range(4))
    chunks = read_file(_write(tmp_path, "long.md", f"# Book\n\n## Ch\n\n{body}"), tmp_path)
    assert len(chunks) >= 2
    assert all(c.location == "Book > Ch" for c in chunks)
    assert all(len(c.text) <= 2000 for c in chunks)
    assert "paragraph 0" in chunks[0].text and "paragraph 3" in chunks[-1].text


def test_embedded_text_includes_context_header(tmp_path):
    spe = read_file(_write(tmp_path, "grimorio.md", GRIMOIRE), tmp_path)[1]
    assert spe.embedded_text.startswith(
        "Grimório | Grimório > 1. ARCHI > 1.1 Posição do arco > Sul ponticello estremo (s.p.e.) | strings\n"
    )


def test_ids_are_unique_and_deterministic(tmp_path):
    f = _write(tmp_path, "grimorio.md", GRIMOIRE)
    ids = [c.id for c in read_file(f, tmp_path)]
    assert len(set(ids)) == len(ids)
    assert ids == [c.id for c in read_file(f, tmp_path)]


def _pdf(pages: list[str]) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


def test_pdf_preserves_page_numbers(tmp_path):
    chunks = read_file(_write(tmp_path, "woodwinds/Clarinet Treatise.pdf", _pdf(["multiphonic", "slap"])), tmp_path)
    assert [(c.location, c.text.strip()) for c in chunks] == [("p. 1", "multiphonic"), ("p. 2", "slap")]
    assert chunks[0].source == "Clarinet Treatise"
    assert chunks[0].kind == "book" and chunks[0].family == "woodwinds"


def test_txt_uses_overlapping_windows(tmp_path):
    chunks = read_file(_write(tmp_path, "notes.txt", "a" * 2500), tmp_path)
    assert len(chunks) == 3
    assert all(len(c.text) <= 1000 for c in chunks)


def _measure(n, content="", attrs=""):
    return f'<measure number="{n}">{attrs}{content}</measure>'


def _words_xml(t):
    return f"<direction><direction-type><words>{t}</words></direction-type></direction>"


def _note_mxl(step="E", octave=5, notehead="", notations=""):
    nh = f"<notehead>{notehead}</notehead>" if notehead else ""
    nt = f"<notations>{notations}</notations>" if notations else ""
    return f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch><duration>4</duration><type>whole</type>{nh}{nt}</note>"


def _score_mxl(title, parts: dict[str, list[str]]) -> str:
    part_list = "".join(f'<score-part id="P{i}"><part-name>{name}</part-name></score-part>' for i, name in enumerate(parts, 1))
    body = "".join(f'<part id="P{i}">{"".join(ms)}</part>' for i, ms in enumerate(parts.values(), 1))
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
        f"<work><work-title>{title}</work-title></work><part-list>{part_list}</part-list>{body}</score-partwise>"
    )


ATTR = (
    "<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>"
    '<clef><sign>G</sign><line>2</line></clef></attributes>'
    "<direction><direction-type><metronome><beat-unit>quarter</beat-unit>"
    "<per-minute>44</per-minute></metronome></direction-type></direction>"
)


def test_musicxml_produces_work_card_and_measure_cards(tmp_path):
    vn = [_measure(1, _note_mxl(), ATTR)] + [_measure(n, _note_mxl()) for n in range(2, 8)]
    vn[6] = _measure(
        7,
        _words_xml("scratch")
        + "<direction><direction-type><dynamics><ff/></dynamics></direction-type></direction>"
        + _note_mxl(notehead="x", notations='<ornaments><tremolo type="single">3</tremolo></ornaments>'),
    )
    vc = [_measure(1, _note_mxl("G", 3), ATTR)] + [_measure(n, _note_mxl("G", 3)) for n in range(2, 8)]
    vc[1] = _measure(2, _words_xml("sul pont. estr.") + _note_mxl("G", 3, notehead="diamond"))
    xml = _score_mxl("Quarteto delle Pressioni", {"Violino I": vn, "Violoncello": vc})
    chunks = read_file(_write(tmp_path, "06_quarteto.musicxml", xml), tmp_path)

    card = chunks[0]
    assert card.location == "work"
    assert "Violino I, Violoncello" in card.text and "7 measures" in card.text
    assert "♩=44" in card.text and "4/4" in card.text
    assert card.source == "Quarteto delle Pressioni" and card.work == "06_quarteto"
    assert card.kind == "score" and card.family == "strings"

    by_loc = {c.location: c.text for c in chunks[1:]}
    assert list(by_loc) == ["m.1–3", "m.3–5", "m.5–7"]
    assert "Violoncello, measure 2: sul pont. estr.; notehead diamond" in by_loc["m.1–3"]
    assert "Violino I, measure 7: scratch; notehead x; tremolo; ff" in by_loc["m.5–7"]
    assert "scratch" not in by_loc["m.1–3"]


def test_md_with_sibling_score_becomes_work_analysis(tmp_path):
    _write(tmp_path, "06_quarteto.musicxml", _score_mxl("Q", {"Viola": [_measure(1, _note_mxl())]}))
    chunks = read_file(_write(tmp_path, "06_quarteto.md", "# Quartet\n\n## Form\n\nA B C"), tmp_path)
    assert {c.kind for c in chunks} == {"work_analysis"}
    assert {c.work for c in chunks} == {"06_quarteto"}
    assert {c.family for c in chunks} == {"strings"}


def test_numbered_dictionary_produces_one_example_per_technique(tmp_path):
    vn = [
        _measure(1, _words_xml("1. sul pont. (trem.)") + _note_mxl(), ATTR),
        _measure(2, _words_xml("2. col legno batt.") + _note_mxl(notehead="x")),
        _measure(3, _note_mxl()),
    ]
    cl = [_measure(1, _note_mxl(), ATTR), _measure(2, _note_mxl()), _measure(3, _words_xml("3. frullato (flz.)") + _note_mxl())]
    xml = _score_mxl("Dizionario", {"Violino": vn, "Clarinetto in Sib": cl})
    chunks = read_file(_write(tmp_path, "snippets.musicxml", xml), tmp_path)

    assert [(c.location, c.family) for c in chunks] == [
        ("1. sul pont. (trem.)", "strings"),
        ("2. col legno batt.", "strings"),
        ("3. frullato (flz.)", "woodwinds"),
    ]
    clb = chunks[1]
    assert clb.kind == "notated_example"
    assert "Violino, measure 2" in clb.text and "notehead x" in clb.text
    assert clb.extra["part"] == "Violino"
    assert '<measure number="2">' in clb.extra["musicxml"]
    assert "<divisions>1</divisions>" in clb.extra["musicxml"]


def test_unknown_extension_is_ignored(tmp_path):
    assert read_file(_write(tmp_path, "piece.mscz", b"zip"), tmp_path) == []


def test_family_uses_deepest_heading(tmp_path):
    md = "# Winds and strings\n\n## LEGNI\n\n#### Corno inglese\n\nx\n\n## ARCHI\n\n#### Violino\n\ny"
    assert [c.family for c in read_file(_write(tmp_path, "m.md", md), tmp_path)] == ["woodwinds", "strings"]


def test_score_with_single_numbered_text_does_not_become_dictionary(tmp_path):
    vn = [_measure(1, _words_xml("1. Solo") + _note_mxl(), ATTR), _measure(2, _note_mxl()), _measure(3, _note_mxl())]
    chunks = read_file(_write(tmp_path, "piece.musicxml", _score_mxl("Piece", {"Violino": vn})), tmp_path)
    assert [c.kind for c in chunks] == ["score", "score"]


def test_windows_follow_longest_part(tmp_path):
    short = [_measure(1, _note_mxl(), ATTR)]
    long = [_measure(1, _note_mxl(), ATTR)] + [_measure(n, _note_mxl()) for n in range(2, 6)]
    long[4] = _measure(5, _words_xml("scratch") + _note_mxl())
    chunks = read_file(_write(tmp_path, "p.musicxml", _score_mxl("P", {"Flauto": short, "Viola": long})), tmp_path)
    assert [c.location for c in chunks[1:]] == ["m.1–3", "m.3–5"]
    assert "Viola, measure 5: scratch" in chunks[-1].text


def _doc(chunk):
    import xml.etree.ElementTree as ET

    return ET.fromstring(chunk.extra["musicxml"])


def test_measure_card_carries_standalone_musicxml(tmp_path):
    vn = [_measure(1, _note_mxl(), ATTR)] + [_measure(n, _note_mxl()) for n in range(2, 6)]
    vc = [_measure(1, _note_mxl("G", 3), ATTR)] + [_measure(n, _note_mxl("G", 3)) for n in range(2, 6)]
    chunks = read_file(_write(tmp_path, "q.musicxml", _score_mxl("Q", {"Violino I": vn, "Violoncello": vc})), tmp_path)
    card = next(c for c in chunks if c.location == "m.3–5")
    doc = _doc(card)
    assert doc.tag == "score-partwise"
    assert [p.findtext("part-name") for p in doc.iter("score-part")] == ["Violino I", "Violoncello"]
    for part in doc.findall("part"):
        assert [m.get("number") for m in part.findall("measure")] == ["3", "4", "5"]
        assert part.find("measure/attributes/divisions") is not None
    assert "musicxml" not in chunks[0].extra


def test_numbered_example_carries_standalone_part_document(tmp_path):
    vn = [_measure(1, _words_xml("1. a") + _note_mxl(), ATTR), _measure(2, _words_xml("2. b") + _note_mxl()), _measure(3, _words_xml("3. c") + _note_mxl())]
    chunks = read_file(_write(tmp_path, "s.musicxml", _score_mxl("Dizionario", {"Violino": vn})), tmp_path)
    doc = _doc(chunks[1])
    assert [p.findtext("part-name") for p in doc.iter("score-part")] == ["Violino"]
    assert [m.get("number") for m in doc.iter("measure")] == ["2"]


def test_composer_comes_from_nearest_corpus_toml(tmp_path):
    _write(tmp_path, "works/_corpus.toml", 'composer = "Filipe Borato"\n')
    _write(tmp_path, "_corpus.toml", 'composer = "Other"\n')
    own = read_file(_write(tmp_path, "works/etude.md", "# Etude\n\nx"), tmp_path)
    other = read_file(_write(tmp_path, "books/notes.md", "# Notes\n\ny"), tmp_path)
    assert own[0].composer == "Filipe Borato"
    assert other[0].composer == "Other"
    assert "Filipe Borato" in own[0].embedded_text.splitlines()[0]


def test_without_corpus_toml_composer_is_empty(tmp_path):
    assert read_file(_write(tmp_path, "notes.md", "# N\n\nx"), tmp_path)[0].composer == ""


def test_card_inherits_partial_attributes_from_first_measure(tmp_path):
    clef_only = "<attributes><clef><sign>F</sign><line>4</line></clef></attributes>"
    vn = [_measure(1, _note_mxl(), ATTR), _measure(2, _note_mxl()), _measure(3, _note_mxl(), clef_only), _measure(4, _note_mxl()), _measure(5, _note_mxl())]
    chunks = read_file(_write(tmp_path, "q.musicxml", _score_mxl("Q", {"Violino": vn})), tmp_path)
    attrs = _doc(next(c for c in chunks if c.location == "m.3–5")).find("part/measure/attributes")
    assert attrs is not None
    assert attrs.findtext("divisions") == "1"
    assert attrs.findtext("clef/sign") == "F"

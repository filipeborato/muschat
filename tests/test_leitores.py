from pathlib import Path

from muschat.rag.leitores import ler

GRIMORIO = """\
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
# isto não é cabeçalho
```

## 2. LEGNI

#### Frullato (flz.)
- **Gráfica:** "flz."
"""


def _escrever(tmp_path: Path, nome: str, conteudo: str | bytes) -> Path:
    arq = tmp_path / nome
    arq.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(conteudo, bytes):
        arq.write_bytes(conteudo)
    else:
        arq.write_text(conteudo, encoding="utf-8")
    return arq


def test_markdown_vira_um_chunk_por_cabecalho_com_caminho(tmp_path):
    chunks = ler(_escrever(tmp_path, "grimorio.md", GRIMORIO), tmp_path)
    locais = [c.local for c in chunks]
    assert locais == [
        "Grimório",
        "Grimório > 1. ARCHI > 1.1 Posição do arco > Sul ponticello estremo (s.p.e.)",
        "Grimório > 1. ARCHI > 1.1 Posição do arco > Sul tasto (s.t.)",
        "Grimório > 2. LEGNI > Frullato (flz.)",
    ]
    spe = chunks[1]
    assert "Sciarrino" in spe.texto and "Sul tasto" not in spe.texto
    assert "# isto não é cabeçalho" in chunks[2].texto
    assert spe.fonte == "Grimório"
    assert spe.tipo == "texto"


def test_familia_vem_do_caminho_ou_da_pasta(tmp_path):
    chunks = ler(_escrever(tmp_path, "grimorio.md", GRIMORIO), tmp_path)
    assert [c.familia for c in chunks] == ["", "cordas", "cordas", "sopros"]
    outro = ler(_escrever(tmp_path, "cordas/notas.md", "# Notas\n\ntexto"), tmp_path)
    assert outro[0].familia == "cordas"


def test_secao_longa_quebra_por_paragrafo_sem_perder_caminho(tmp_path):
    corpo = "\n\n".join(f"parágrafo {i} " + "x" * 900 for i in range(4))
    chunks = ler(_escrever(tmp_path, "longo.md", f"# Livro\n\n## Cap\n\n{corpo}"), tmp_path)
    assert len(chunks) >= 2
    assert all(c.local == "Livro > Cap" for c in chunks)
    assert all(len(c.texto) <= 2000 for c in chunks)
    assert "parágrafo 0" in chunks[0].texto and "parágrafo 3" in chunks[-1].texto


def test_texto_embutido_tem_cabecalho_de_contexto(tmp_path):
    spe = ler(_escrever(tmp_path, "grimorio.md", GRIMORIO), tmp_path)[1]
    assert spe.texto_embutido.startswith(
        "Grimório | Grimório > 1. ARCHI > 1.1 Posição do arco > Sul ponticello estremo (s.p.e.) | cordas\n"
    )


def test_ids_sao_unicos_e_estaveis(tmp_path):
    arq = _escrever(tmp_path, "grimorio.md", GRIMORIO)
    ids = [c.id for c in ler(arq, tmp_path)]
    assert len(set(ids)) == len(ids)
    assert ids == [c.id for c in ler(arq, tmp_path)]


def _pdf(paginas: list[str]) -> bytes:
    """PDF mínimo válido, uma página por string (fonte Helvetica)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for texto in paginas:
        stream = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
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


def test_pdf_preserva_pagina(tmp_path):
    chunks = ler(_escrever(tmp_path, "sopros/Tratado Clarinete.pdf", _pdf(["multifonico", "slap"])), tmp_path)
    assert [(c.local, c.texto.strip()) for c in chunks] == [("p. 1", "multifonico"), ("p. 2", "slap")]
    assert chunks[0].fonte == "Tratado Clarinete"
    assert chunks[0].tipo == "livro" and chunks[0].familia == "sopros"


def test_txt_em_janelas_com_sobreposicao(tmp_path):
    chunks = ler(_escrever(tmp_path, "notas.txt", "a" * 2500), tmp_path)
    assert len(chunks) == 3
    assert all(len(c.texto) <= 1000 for c in chunks)


def _compasso(n, conteudo="", atributos=""):
    return f'<measure number="{n}">{atributos}{conteudo}</measure>'


def _palavra(t):
    return f"<direction><direction-type><words>{t}</words></direction-type></direction>"


def _nota(step="E", octave=5, notehead="", notations=""):
    nh = f"<notehead>{notehead}</notehead>" if notehead else ""
    nt = f"<notations>{notations}</notations>" if notations else ""
    return (f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
            f"<duration>4</duration><type>whole</type>{nh}{nt}</note>")


def _partitura(titulo, partes: dict[str, list[str]]) -> str:
    lista = "".join(f'<score-part id="P{i}"><part-name>{nome}</part-name></score-part>'
                    for i, nome in enumerate(partes, 1))
    corpo = "".join(f'<part id="P{i}">{"".join(ms)}</part>' for i, ms in enumerate(partes.values(), 1))
    return (f'<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
            f"<work><work-title>{titulo}</work-title></work><part-list>{lista}</part-list>{corpo}"
            f"</score-partwise>")


ATTR = ("<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>"
        "<clef><sign>G</sign><line>2</line></clef></attributes>"
        "<direction><direction-type><metronome><beat-unit>quarter</beat-unit>"
        "<per-minute>44</per-minute></metronome></direction-type></direction>")


def test_musicxml_gera_ficha_da_obra_e_fichas_de_compasso(tmp_path):
    vn = [_compasso(1, _nota(), ATTR)] + [_compasso(n, _nota()) for n in range(2, 8)]
    vn[6] = _compasso(7, _palavra("scratch") + "<direction><direction-type><dynamics><ff/></dynamics>"
                         "</direction-type></direction>"
                      + _nota(notehead="x", notations='<ornaments><tremolo type="single">3</tremolo></ornaments>'))
    vc = [_compasso(1, _nota("G", 3), ATTR)] + [_compasso(n, _nota("G", 3)) for n in range(2, 8)]
    vc[1] = _compasso(2, _palavra("sul pont. estr.") + _nota("G", 3, notehead="diamond"))
    xml = _partitura("Quarteto delle Pressioni", {"Violino I": vn, "Violoncello": vc})
    chunks = ler(_escrever(tmp_path, "06_quarteto.musicxml", xml), tmp_path)

    ficha = chunks[0]
    assert ficha.local == "obra"
    assert "Violino I, Violoncello" in ficha.texto and "7 compassos" in ficha.texto
    assert "♩=44" in ficha.texto and "4/4" in ficha.texto
    assert ficha.fonte == "Quarteto delle Pressioni" and ficha.obra == "06_quarteto"
    assert ficha.tipo == "partitura" and ficha.familia == "cordas"

    por_local = {c.local: c.texto for c in chunks[1:]}
    assert list(por_local) == ["m.1–3", "m.3–5", "m.5–7"]
    assert "Violoncello m.2: sul pont. estr.; notehead diamond" in por_local["m.1–3"]
    assert "Violino I m.7: scratch; notehead x; tremolo; ff" in por_local["m.5–7"]
    assert "scratch" not in por_local["m.1–3"]


def test_md_com_partitura_irma_vira_analise_da_obra(tmp_path):
    _escrever(tmp_path, "06_quarteto.musicxml", _partitura("Q", {"Viola": [_compasso(1, _nota())]}))
    chunks = ler(_escrever(tmp_path, "06_quarteto.md", "# Quarteto\n\n## Forma\n\nA B C"), tmp_path)
    assert {c.tipo for c in chunks} == {"analise_obra"}
    assert {c.obra for c in chunks} == {"06_quarteto"}
    assert {c.familia for c in chunks} == {"cordas"}  # herdada da partitura irmã


def test_dicionario_numerado_gera_um_exemplo_por_tecnica(tmp_path):
    vn = [_compasso(1, _palavra("1. sul pont. (trem.)") + _nota(), ATTR),
          _compasso(2, _palavra("2. col legno batt.") + _nota(notehead="x")),
          _compasso(3, _nota())]
    cl = [_compasso(1, _nota(), ATTR), _compasso(2, _nota()), _compasso(3, _palavra("3. frullato (flz.)") + _nota())]
    xml = _partitura("Dizionario", {"Violino": vn, "Clarinetto in Sib": cl})
    chunks = ler(_escrever(tmp_path, "snippets.musicxml", xml), tmp_path)

    assert [(c.local, c.familia) for c in chunks] == [
        ("1. sul pont. (trem.)", "cordas"),
        ("2. col legno batt.", "cordas"),
        ("3. frullato (flz.)", "sopros"),
    ]
    clb = chunks[1]
    assert clb.tipo == "exemplo_notado"
    assert "Violino m.2" in clb.texto and "notehead x" in clb.texto
    assert clb.extra["parte"] == "Violino"
    assert '<measure number="2">' in clb.extra["musicxml"]
    assert "<divisions>1</divisions>" in clb.extra["musicxml"]  # atributos herdados p/ renderizar


def test_extensao_desconhecida_e_ignorada(tmp_path):
    assert ler(_escrever(tmp_path, "peca.mscz", b"zip"), tmp_path) == []


def test_familia_usa_o_cabecalho_mais_profundo(tmp_path):
    md = "# Sopros e cordas\n\n## LEGNI\n\n#### Corno inglese\n\nx\n\n## ARCHI\n\n#### Violino\n\ny"
    assert [c.familia for c in ler(_escrever(tmp_path, "m.md", md), tmp_path)] == ["sopros", "cordas"]


def test_partitura_com_um_texto_numerado_nao_vira_dicionario(tmp_path):
    vn = [_compasso(1, _palavra("1. Solo") + _nota(), ATTR), _compasso(2, _nota()), _compasso(3, _nota())]
    chunks = ler(_escrever(tmp_path, "peca.musicxml", _partitura("Peça", {"Violino": vn})), tmp_path)
    assert [c.tipo for c in chunks] == ["partitura", "partitura"]


def test_janelas_seguem_a_parte_mais_longa(tmp_path):
    curta = [_compasso(1, _nota(), ATTR)]
    longa = [_compasso(1, _nota(), ATTR)] + [_compasso(n, _nota()) for n in range(2, 6)]
    longa[4] = _compasso(5, _palavra("scratch") + _nota())
    chunks = ler(_escrever(tmp_path, "p.musicxml", _partitura("P", {"Flauto": curta, "Viola": longa})), tmp_path)
    assert [c.local for c in chunks[1:]] == ["m.1–3", "m.3–5"]
    assert "Viola m.5: scratch" in chunks[-1].texto

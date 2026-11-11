"""Tabela única de técnicas estendidas (vocabulário do grimório), compartilhada por todos os adaptadores."""

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Tecnica:
    texto: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False


TECNICAS = {
    "sul ponticello": Tecnica("sul pont."),
    "sul ponticello estremo": Tecnica("sul pont. estr."),
    "sul tasto": Tecnica("sul tasto"),
    "flautando": Tecnica("flautando"),
    "col legno battuto": Tecnica("c.l. batt.", "x"),
    "col legno tratto": Tecnica("c.l. tratto", "x"),
    "dietro il ponticello": Tecnica("dietro il pont.", "x"),
    "sulla cordiera": Tecnica("sulla cordiera", "x"),
    "scratch": Tecnica("scratch", "x"),
    "pizzicato": Tecnica("pizz."),
    "pizz. bartok": Tecnica(technical="snap-pizzicato"),
    "harmonico": Tecnica(notehead="diamond"),  # grimório: losango, sem o círculo redundante
    "fischio": Tecnica("fischio", "diamond"),
    "whistle tone": Tecnica("whistle tone", "diamond"),
    "tremolo": Tecnica(tremolo=True),
    "frullato": Tecnica("flz.", tremolo=True),
    "multifonico": Tecnica("multif."),
    "slap": Tecnica("slap", "x"),
    "key click": Tecnica("key click", "x"),
    "aria": Tecnica("aria", "circle-x"),
}
ALIASES = {"harmonic": "harmonico", "armonico": "harmonico", "arm.": "harmonico", "arm. naturale": "harmonico",
           "flatterzunge": "frullato", "flz.": "frullato", "snap pizzicato": "pizz. bartok",
           "bartok pizzicato": "pizz. bartok", "c.l. batt.": "col legno battuto", "c.l.b.": "col legno battuto",
           "c.l. tratto": "col legno tratto", "c.l.t.": "col legno tratto", "s.p.e.": "sul ponticello estremo",
           "sul pont.": "sul ponticello", "sul pont. estr.": "sul ponticello estremo", "s.t.": "sul tasto",
           "multiphonic": "multifonico", "suono eolico": "aria", "chiavi": "key click"}


def tecnica(nome: str) -> Tecnica:
    chave = unicodedata.normalize("NFKD", nome.strip().lower()).encode("ascii", "ignore").decode()
    chave = ALIASES.get(chave, chave)
    return TECNICAS.get(chave) or Tecnica(texto=nome.strip())  # desconhecida: vira texto, nunca falha

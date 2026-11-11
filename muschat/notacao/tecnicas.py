"""Tabela única de técnicas estendidas (vocabulário do grimório), compartilhada por todos os adaptadores."""

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Tecnica:
    texto: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False
    articulacao: str | None = None  # nome MusicXML (<articulations>)


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
    "staccato": Tecnica(articulacao="staccato"),
    "staccatissimo": Tecnica(articulacao="staccatissimo"),
    "tenuto": Tecnica(articulacao="tenuto"),
    "accento": Tecnica(articulacao="accent"),
    "marcato": Tecnica(articulacao="strong-accent"),
}
ALIASES = {"harmonic": "harmonico", "armonico": "harmonico", "arm.": "harmonico", "arm. naturale": "harmonico",
           "flatterzunge": "frullato", "flz.": "frullato", "snap pizzicato": "pizz. bartok",
           "bartok pizzicato": "pizz. bartok", "c.l. batt.": "col legno battuto", "c.l.b.": "col legno battuto",
           "c.l. tratto": "col legno tratto", "c.l.t.": "col legno tratto", "s.p.e.": "sul ponticello estremo",
           "sul pont.": "sul ponticello", "sul pont. estr.": "sul ponticello estremo", "s.t.": "sul tasto",
           "multiphonic": "multifonico", "suono eolico": "aria", "chiavi": "key click", "stacc.": "staccato",
           "accent": "accento", "acento": "accento", "ten.": "tenuto"}


class Indicacoes:
    """Indicações escritas de uma voz, evento a evento: a técnica vale até mudar (a volta é "ord.", ou "arco"
    depois de pizz.), a dinâmica até a próxima marca, e o texto do evento não repete o da técnica."""

    def __init__(self) -> None:
        self.textos: tuple[str, ...] = ()
        self.dinamica: str | None = None

    def proximo(self, tecnicas: list[Tecnica], nota: bool, texto: str | None,
                dinamica: str | None) -> tuple[list[str], list[str], str | None]:
        """(textos em itálico da técnica, textos livres, dinâmica nova) a escrever neste evento."""
        italicos: list[str] = []
        textos = tuple(t.texto for t in tecnicas if t.texto)
        if nota and textos != self.textos:  # pausa não muda a técnica em curso
            italicos = list(textos) or ["arco" if "pizz." in self.textos else "ord."]
            self.textos = textos
        livres = [texto] if texto and tecnica(texto).texto not in textos else []
        nova = dinamica if dinamica and dinamica != self.dinamica else None
        if nova:
            self.dinamica = nova
        return italicos, livres, nova


def tecnica(nome: str) -> Tecnica:
    chave = unicodedata.normalize("NFKD", nome.strip().lower()).encode("ascii", "ignore").decode()
    chave = ALIASES.get(chave, chave)
    return TECNICAS.get(chave) or Tecnica(texto=nome.strip())  # desconhecida: vira texto, nunca falha

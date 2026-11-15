"""Tabela única de técnicas estendidas (vocabulário do grimório), compartilhada por todos os adaptadores."""

import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction

from muschat.instrumentos import FAMILIAS, INSTRUMENTOS, Familia, identificar
from muschat.musicir import MusicIR, Nota


@dataclass(frozen=True)
class Tecnica:
    texto: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False
    articulacao: str | None = None  # nome MusicXML (<articulations>)
    familias: frozenset[Familia] = frozenset(FAMILIAS)  # onde a técnica se toca; padrão: qualquer instrumento
    so: frozenset[str] = frozenset()  # quando só alguns instrumentos da família tocam (chaves de INSTRUMENTOS)
    # idiomatismo (tabela inicial, a revisar com o compositor): registro como fração da tessitura do instrumento
    # (0 = nota mais grave, 1 = mais aguda), duração mínima que soa, e se a técnica sustenta altura precisa
    registro: tuple[float, float] = (0.0, 1.0)
    duracao_minima: Fraction = Fraction(0)
    altura_precisa: bool = True


def _em(*familias: Familia) -> frozenset[Familia]:
    return frozenset(familias)


_CORDAS, _SOPROS, _METAIS, _MADEIRAS = _em("cordas"), _em("madeiras", "metais"), _em("metais"), _em("madeiras")
_COLCHEIA, _SEMINIMA = Fraction(1, 8), Fraction(1, 4)
_PERCUSSAO, _TECLADO, _HARPA, _VIOLAO, _VOZ = _em("percussao"), _em("teclado"), _em("harpa"), _em("violao"), _em("voz")
_RESSOA = _em("percussao", "harpa", "teclado", "violao")
_FLAUTAS = frozenset({"piccolo", "flauta", "flauta contralto", "flauta baixo"})
_PALHETA_SIMPLES = frozenset({"requinta", "clarinete", "clarinete baixo", "clarone", "clarinete contrabaixo",
                              "saxofone soprano", "saxofone alto", "saxofone tenor", "saxofone baritono"})
_PIANO = frozenset({"piano"})
_TAMBORES = frozenset({"caixa", "tom-tons", "bongos", "congas", "bumbo", "timpanos"})
_ARCO_PERC = frozenset({"vibrafone", "crotalos", "marimba", "pratos", "prato suspenso", "tam-tam", "gongo"})

TECNICAS = {
    # cordas friccionadas
    "sul ponticello": Tecnica("sul pont.", familias=_CORDAS),
    "sul ponticello estremo": Tecnica("sul pont. estr.", familias=_CORDAS),
    "sul tasto": Tecnica("sul tasto", familias=_CORDAS | _VIOLAO),
    "flautando": Tecnica("flautando", familias=_CORDAS),
    "col legno battuto": Tecnica("c.l. batt.", "x", familias=_CORDAS),
    "col legno tratto": Tecnica("c.l. tratto", "x", familias=_CORDAS),
    "dietro il ponticello": Tecnica("dietro il pont.", "x", familias=_CORDAS),
    "sulla cordiera": Tecnica("sulla cordiera", "x", familias=_CORDAS),
    "scratch": Tecnica("scratch", "x", familias=_CORDAS),
    "pizzicato": Tecnica("pizz.", familias=_CORDAS),
    "pizz. bartok": Tecnica(technical="snap-pizzicato", familias=_CORDAS | _VIOLAO),
    "pizzicato de mao esquerda": Tecnica(technical="stopped", familias=_CORDAS),
    "jete": Tecnica("jeté", familias=_CORDAS, duracao_minima=_COLCHEIA),
    "ricochet": Tecnica("ricochet", familias=_CORDAS, duracao_minima=_COLCHEIA),
    "con sordino": Tecnica("con sord.", familias=_CORDAS | _METAIS),
    "senza sordino": Tecnica("senza sord.", familias=_CORDAS | _METAIS),
    "molto vibrato": Tecnica("molto vib.", familias=_CORDAS | _SOPROS | _VOZ),
    "senza vibrato": Tecnica("senza vib.", familias=_CORDAS | _SOPROS | _VOZ),
    "percussao no corpo": Tecnica("percuss. corpo", "x", familias=_CORDAS | _VIOLAO),
    "harmonico": Tecnica(notehead="diamond", familias=_em("cordas", "madeiras", "metais", "harpa", "violao"),
                         registro=(0.2, 1.0)),  # grimório: losango, sem o círculo
    # madeiras e metais
    "fischio": Tecnica("fischio", "diamond", familias=_MADEIRAS, so=_FLAUTAS, registro=(0.35, 1.0),
                       duracao_minima=_SEMINIMA),
    "whistle tone": Tecnica("whistle tone", "diamond", familias=_MADEIRAS, so=_FLAUTAS, registro=(0.35, 1.0),
                            duracao_minima=_SEMINIMA),
    "jet whistle": Tecnica("jet wh.", familias=_MADEIRAS, so=_FLAUTAS, duracao_minima=_COLCHEIA,
                           altura_precisa=False),
    "tongue ram": Tecnica("tongue ram", "triangle", familias=_MADEIRAS, so=_FLAUTAS, registro=(0.0, 0.4)),
    "frullato": Tecnica("flz.", tremolo=True, familias=_SOPROS | _VOZ, registro=(0.0, 0.9),
                        duracao_minima=Fraction(1, 16)),
    "multifonico": Tecnica("multif.", familias=_SOPROS | _VOZ, registro=(0.0, 0.7), duracao_minima=_COLCHEIA),
    "slap": Tecnica("slap", "x", familias=_SOPROS, registro=(0.0, 0.55)),
    "key click": Tecnica("key click", "x", familias=_MADEIRAS),
    "aria": Tecnica("aria", "circle-x", familias=_SOPROS | _VOZ, duracao_minima=_COLCHEIA),
    "bisbigliando": Tecnica("bisb.", familias=_MADEIRAS | _HARPA),
    "canta e suona": Tecnica("canta e suona", familias=_SOPROS),
    "subtone": Tecnica("subtone", familias=_MADEIRAS, so=_PALHETA_SIMPLES, registro=(0.0, 0.5)),
    "growl": Tecnica("growl", familias=_SOPROS),
    "pizzicato de lingua": Tecnica("t. pizz.", familias=_MADEIRAS),
    # metais
    "gestopft": Tecnica("gestopft", technical="stopped", familias=_METAIS, registro=(0.2, 0.9)),
    "aberto": Tecnica(technical="open", familias=_METAIS),
    "cuivre": Tecnica("cuivré", familias=_METAIS),
    "surdina harmon": Tecnica("harmon mute", familias=_METAIS),
    "surdina cup": Tecnica("cup mute", familias=_METAIS),
    "surdina plunger": Tecnica("plunger", familias=_METAIS),
    "meia valvula": Tecnica("½ valv.", familias=_METAIS),
    "so bocal": Tecnica("solo bocchino", "x", familias=_METAIS),
    "shake": Tecnica("shake", tremolo=True, familias=_METAIS),
    # percussão
    "rulo": Tecnica(tremolo=True, familias=_PERCUSSAO),
    "laissez vibrer": Tecnica("l.v.", familias=_RESSOA),
    "secco": Tecnica("secco", familias=_RESSOA),
    "dead stroke": Tecnica("dead stroke", familias=_PERCUSSAO),
    "rim shot": Tecnica("rim shot", "x", familias=_PERCUSSAO, so=_TAMBORES),
    "sul bordo": Tecnica("sul bordo", familias=_PERCUSSAO),
    "com arco": Tecnica("con l'arco", "diamond", familias=_PERCUSSAO, so=_ARCO_PERC),
    "baqueta dura": Tecnica("bacch. dure", familias=_PERCUSSAO),
    "baqueta macia": Tecnica("bacch. morbide", familias=_PERCUSSAO),
    "vassourinha": Tecnica("spazzola", familias=_PERCUSSAO, so=_TAMBORES | {"prato suspenso"}),
    # teclado (piano)
    "pizzicato nas cordas": Tecnica("pizz. sulle corde", familias=_TECLADO, so=_PIANO),
    "cordas abafadas": Tecnica("muted", technical="stopped", familias=_TECLADO, so=_PIANO),
    "harmonico no piano": Tecnica(notehead="diamond", familias=_TECLADO, so=_PIANO),
    "cluster": Tecnica("cluster", familias=_TECLADO),
    "glissando nas cordas": Tecnica("gliss. sulle corde", familias=_TECLADO, so=_PIANO),
    "una corda": Tecnica("una corda", familias=_TECLADO, so=_PIANO),
    "piano preparado": Tecnica("prep.", "x", familias=_TECLADO, so=_PIANO),
    # harpa
    "pres de la table": Tecnica("p.d.l.t.", familias=_HARPA),
    "xilofonico": Tecnica("sons xyl.", "x", familias=_HARPA),
    "com a unha": Tecnica("avec l'ongle", familias=_HARPA | _VIOLAO),
    "glissando de pedal": Tecnica("pedal gliss.", familias=_HARPA),
    # violão
    "tambora": Tecnica("tambora", "x", familias=_VIOLAO),
    "rasgueado": Tecnica("rasg.", familias=_VIOLAO),
    "golpe": Tecnica("golpe", "x", familias=_VIOLAO),
    "palm mute": Tecnica("p.m.", familias=_VIOLAO),
    # voz
    "sprechstimme": Tecnica("Sprechst.", "x", familias=_VOZ),
    "bocca chiusa": Tecnica("b.ch.", familias=_VOZ),
    "falsetto": Tecnica("falsetto", familias=_VOZ),
    "sussurrado": Tecnica("sussurrato", "x", familias=_VOZ),
    "ingressivo": Tecnica("ingr.", familias=_VOZ),
    "vocal fry": Tecnica("vocal fry", familias=_VOZ),
    "estalo de lingua": Tecnica("tongue click", "x", familias=_VOZ | _MADEIRAS),
    # qualquer instrumento
    "tremolo": Tecnica(tremolo=True, duracao_minima=_COLCHEIA),
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
           "accent": "accento", "acento": "accento", "ten.": "tenuto", "jete": "jete", "con sord.": "con sordino",
           "senza sord.": "senza sordino", "flutter tongue": "frullato", "stopped": "gestopft", "bouche": "gestopft",
           "harmon": "surdina harmon", "half valve": "meia valvula", "l.v.": "laissez vibrer",
           "p.d.l.t.": "pres de la table", "bisb.": "bisbigliando", "sprechgesang": "sprechstimme",
           "b.ch.": "bocca chiusa", "sing and play": "canta e suona", "cantar e tocar": "canta e suona",
           "sussurrato": "sussurrado", "roll": "rulo", "rufo": "rulo", "airtone": "aria", "air tone": "aria",
           "som de ar": "aria", "tongue pizzicato": "pizzicato de lingua", "slap tongue": "slap",
           "left-hand pizzicato": "pizzicato de mao esquerda", "bowed": "com arco"}


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


def tecnicas_da_familia(familia: Familia) -> list[str]:
    """Nomes das técnicas da família; as restritas a alguns instrumentos dizem quais ("whistle tone (só ...)")."""
    return [nome + (f" (só {', '.join(sorted(t.so))})" if t.so else "")
            for nome, t in TECNICAS.items() if familia in t.familias]


def cabe(nome_tecnica: str, nome_instrumento: str) -> bool:
    """A técnica se toca neste instrumento? Instrumento fora da tabela não tem como ser conferido: vale."""
    t = tecnica(nome_tecnica)
    try:
        chave = identificar(nome_instrumento)
    except ValueError:
        return True
    return INSTRUMENTOS[chave].familia in t.familias and (not t.so or chave in t.so)


def problema(nome_tecnica: str, nome_instrumento: str, nota: Nota) -> str | None:
    """Por que a técnica não se toca nesta nota deste instrumento (família, registro, duração, altura), ou None."""
    if not cabe(nome_tecnica, nome_instrumento):
        return "não se toca nesta família"
    t = tecnica(nome_tecnica)
    if nota.duracao < t.duracao_minima:
        return f"curta demais (mínimo {t.duracao_minima})"
    if not t.altura_precisa and any(a.midicents % 100 for a in nota.alturas):
        return "não tem altura precisa (escrita microtonal)"
    try:
        grave, agudo = INSTRUMENTOS[identificar(nome_instrumento)].tessitura
    except ValueError:
        return None  # instrumento fora da tabela: registro não conferível
    if agudo > grave:
        posicoes = [(a.midicents - grave) / (agudo - grave) for a in nota.alturas]
        if max(posicoes) > t.registro[1]:
            return "fora do registro (agudo demais)"
        if min(posicoes) < t.registro[0]:
            return "fora do registro (grave demais)"
    return None


def tecnicas_intocaveis(ir: MusicIR) -> list[str]:
    """Verificador: toda técnica que não se toca onde está, vinda de qualquer motor ("P2 c.3: slap (...): motivo")."""
    inicios = ir.inicios()
    return [f"{p.id} c.{bisect_right(inicios, e.inicio)}: {t} ({p.instrumento}): {motivo}"
            for p, _, e in ir.eventos() if isinstance(e, Nota)
            for t in e.tecnicas if (motivo := problema(t, p.instrumento, e))]


def tecnica(nome: str) -> Tecnica:
    chave = unicodedata.normalize("NFKD", nome.strip().lower()).encode("ascii", "ignore").decode()
    chave = ALIASES.get(chave, chave)
    return TECNICAS.get(chave) or Tecnica(texto=nome.strip())  # desconhecida: vira texto, nunca falha

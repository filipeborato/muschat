"""Patches aprovados do OpenMusic: para cada um, o que vai no entrada.txt (só números, gerados pelo projeto) e como a
saída validada vira MusicIR. Nenhum Lisp é gerado; o patch é um arquivo do projeto em `patches/`.

Convenção da saída: números separados por espaço; `-1` sozinho = o solver não achou solução; `-2` sozinho = o patch
falhou no OpenMusic e gravou a mensagem em erro.txt.
"""

import math
import os
import random
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from muschat import musicir as ir
from muschat.instrumentos import instrumento
from muschat.plano import Plano
from muschat.procedimentos.comum import QUARTETO, Grade, Ids, exigir
from muschat.procedimentos.progressivos import densidade_por_compasso
from muschat.restricoes import Restricao, verificar

SEM_SOLUCAO, FALHOU = -1.0, -2.0
LIMITE_COMPONENTES = 5000  # parciais enviados ao OMChroma numa tarefa
TIMEOUT_CSOUND = 300  # s
LIMITE_AUDIO = 200_000_000  # bytes de .wav aceitos (~19 min mono 44,1 kHz/16 bits)


@dataclass(frozen=True)
class Contexto:
    """O que a leitura da saída precisa: a tarefa (parâmetros, entrada enviada, pasta) e a lista de etapas."""

    parametros: dict[str, float]
    entrada: str
    pasta: Path
    atual: ir.MusicIR | None
    plano: Plano | None
    rng: random.Random
    proveniencia: ir.Proveniencia


Montar = Callable[[dict[str, float], ir.MusicIR | None, Plano | None], str]
Ler = Callable[[list[float], Contexto], ir.MusicIR]


@dataclass(frozen=True)
class PatchAprovado:
    procedimento: str  # nome no catálogo
    biblioteca: str  # chave normalizada da biblioteca que o patch chama
    funcao_om: str
    descricao: str
    entrada: str  # formato do entrada.txt (para as instruções)
    saida: str  # formato do saida.txt
    passos: tuple[str, ...]
    montar: Montar
    ler: Ler
    autor: str  # vai na proveniência dos eventos importados
    arquivo: str | None = None  # patch do projeto (patches/); None = ainda não escrito
    validado: bool = False  # True só depois de rodar no OpenMusic real com a saída conferida (docs/pesquisa)


def _contar(valores: list[float], n: int) -> None:
    if len(valores) != n:
        raise ValueError(f"a saída tem {len(valores)} valores; a tarefa pediu {n} valores")


def _passos(arquivo: str, extra: str = "") -> tuple[str, ...]:
    return (
        f"Extraia o ZIP inteiro para uma pasta; mantenha entrada.txt e {arquivo} juntos.",
        "Abra o OpenMusic com um workspace.",
        f"No Listener, use Lisp › Load File e selecione {arquivo} da pasta da tarefa.",
        "Confirme o carregamento da biblioteca se solicitado. O script lê entrada.txt e grava saida.txt na mesma "
        f"pasta (e erro.txt, se algo falhar).{extra}",
        "Volte ao Muschat e envie `saida.txt` em “Aguardando você”.",
    )


# --- material: uma voz de semínimas ------------------------------------------------------------------------------------

def material(alturas: list[int], *, titulo: str, proveniencia: ir.Proveniencia) -> ir.MusicIR:
    """Alturas como uma voz de semínimas em 4/4; o último compasso se completa com pausa."""
    compassos = max(1, math.ceil(len(alturas) / 4))
    eventos: list[ir.Evento] = [
        ir.Nota(id=f"M1V1E{k + 1}", inicio=Fraction(k, 4), duracao=Fraction(1, 4), proveniencia=proveniencia,
                alturas=[ir.Altura(midicents=a)]) for k, a in enumerate(alturas)]
    resto = Fraction(compassos) - Fraction(len(alturas), 4)
    if resto:
        eventos.append(ir.Pausa(id=f"M1V1E{len(alturas) + 1}", inicio=Fraction(len(alturas), 4), duracao=resto,
                                proveniencia=proveniencia))
    mediana = sorted(alturas)[len(alturas) // 2] if alturas else 6000
    parte = ir.Parte(id="M1", instrumento="Material espectral", clave="sol" if mediana >= 6000 else "fa",
                     vozes=[ir.Voz(id="M1V1", eventos=eventos)])
    return ir.MusicIR(titulo=titulo, compassos=_compassos(compassos), partes=[parte])


def _compassos(n: int) -> list[ir.Compasso]:
    return [ir.Compasso(numerador=4, denominador=4, andamento=60 if k == 0 else None) for k in range(n)]


# --- Esquisse: série harmônica -----------------------------------------------------------------------------------------

def _serie_montar(p: dict[str, float], atual: ir.MusicIR | None, plano: Plano | None) -> str:
    return f"{int(p['fundamental'])} 1 1 1 {int(p['parciais'])}\n"


def _serie_ler(valores: list[float], ctx: Contexto) -> ir.MusicIR:
    _contar(valores, int(ctx.parametros["parciais"]))
    if fora := [v for v in valores if not 0 <= v <= 12700]:
        raise ValueError(f"valores fora de 0–12700 midicents: {fora[:5]}")
    passo = 100 // 2 ** int(ctx.parametros.get("microtom", 1))
    return material([round(v / passo) * passo for v in valores],
                    titulo=f"Série harmônica sobre {ctx.parametros['fundamental']:.0f} mc (OpenMusic)",
                    proveniencia=ctx.proveniencia)


# --- Chaos: mapa logístico de Verhulst -> densidade por compasso --------------------------------------------------------

def _trecho(atual: ir.MusicIR | None, plano: Plano | None) -> tuple[ir.MusicIR, int, int]:
    atual = exigir(atual)
    if plano is None:
        raise ValueError("a etapa da Chaos precisa do plano (seleção)")
    primeiro, ultimo = Grade(atual).trecho(plano.selecao)
    return atual, primeiro, ultimo


def _chaos_montar(p: dict[str, float], atual: ir.MusicIR | None, plano: Plano | None) -> str:
    _, primeiro, ultimo = _trecho(atual, plano)
    return f"{p['x0']:g} {p['turbulencia']:g} {ultimo - primeiro}\n"  # Verhulst devolve long + 1 valores


def _chaos_ler(valores: list[float], ctx: Contexto) -> ir.MusicIR:
    atual, primeiro, ultimo = _trecho(ctx.atual, ctx.plano)
    _contar(valores, ultimo - primeiro + 1)
    if any(not math.isfinite(v) or abs(v) > 1e6 for v in valores):
        raise ValueError("a série caótica tem valores não finitos ou fora de escala")
    lo, hi, p = min(valores), max(valores), ctx.parametros

    def alvo(n: int) -> float:  # série normalizada 0..1 -> ataques por semínima entre mínimo e máximo
        t = (valores[n - primeiro] - lo) / (hi - lo) if hi > lo else 0.5
        return p["minimo"] + (p["maximo"] - p["minimo"]) * t

    assert ctx.plano is not None
    return densidade_por_compasso(atual, ctx.plano.selecao, alvo, ctx.rng)


# --- Situation: alturas em instrumentos por restrições ------------------------------------------------------------------

def _problema(p: dict[str, float], plano: Plano | None) -> tuple[list[str], list[int], list[list[int]]]:
    """Instrumentos, alturas e domínio de cada vaga: a vaga s é do instrumento s % k, no acorde s // k."""
    nomes = (plano.instrumentos if plano and plano.instrumentos else QUARTETO)
    alturas = [int(p["base"] + k * p["passo"]) for k in range(int(p["alturas"]))]
    if alturas[-1] > 12700:
        raise ValueError(f"as alturas passam de 12700 midicents ({alturas[-1]})")
    tessituras = [instrumento(n).tessitura for n in nomes]
    dominios = [[a for a in alturas if tessituras[s % len(nomes)][0] <= a <= tessituras[s % len(nomes)][1]]
                for s in range(len(alturas))]
    return nomes, alturas, dominios


def _situation_montar(p: dict[str, float], atual: ir.MusicIR | None, plano: Plano | None) -> str:
    """Problema impossível nem vai ao OpenMusic: a busca exaustiva da Situation para provar que não há solução trava o
    OM (visto no teste real). A condição de Hall abaixo decide a existência sozinha."""
    nomes, alturas, dominios = _problema(p, plano)
    condicoes, impossivel = _condicoes(nomes, alturas, dominios)
    if impossivel:
        raise ValueError(f"sem solução, nada enviado ao OpenMusic. Condições: {condicoes}")
    return f"{len(alturas)}\n" + "".join(f"{len(d)} {' '.join(map(str, d))}\n" for d in dominios)


def _condicoes(nomes: list[str], alturas: list[int], dominios: list[list[int]]) -> tuple[str, bool]:
    """As condições do problema e, se não houver solução, o grupo de instrumentos que a impede. Vagas -> alturas
    diferentes é um emparelhamento: pela condição de Hall, há solução sse nenhum grupo tem mais vagas que alcance."""
    linhas = [f"todas as {len(alturas)} alturas ({alturas[0]}–{alturas[-1]} mc) usadas uma vez (sem uníssono)"]
    vagas = [[s for s in range(len(alturas)) if s % len(nomes) == k] for k in range(len(nomes))]
    for k, nome in enumerate(nomes):
        lo, hi = instrumento(nome).tessitura
        linhas.append(f"{nome.capitalize()}: {len(vagas[k])} vagas dentro da tessitura {lo}–{hi} mc")
    # condição de Hall: um grupo de instrumentos com mais vagas do que alturas ao alcance não tem solução
    # shortcut: todos os subconjuntos (2^k), ok até ~16 instrumentos; trocar por fluxo máximo se orquestras vierem
    problemas, culpados = [], []
    for grupo in sorted((g for g in range(1, 2 ** len(nomes))), key=lambda g: bin(g).count("1")):
        membros = [k for k in range(len(nomes)) if grupo >> k & 1]
        if any(c & grupo == c for c in culpados):
            continue  # um subgrupo já explica
        n_vagas = sum(len(vagas[k]) for k in membros)
        alcance = {a for k in membros for s in vagas[k] for a in dominios[s]}
        if len(alcance) < n_vagas:
            culpados.append(grupo)
            quem = " + ".join(nomes[k].capitalize() for k in membros)
            problemas.append(f"{quem} alcançam {len(alcance)} alturas para {n_vagas} vagas")
    fora = [a for a in alturas if not any(a in d for d in dominios)]
    if fora:
        problemas.append(f"nenhum instrumento alcança {fora}")
    return "; ".join(linhas) + (". Impossível: " + "; ".join(problemas) if problemas else ""), bool(problemas)


def _situation_ler(valores: list[float], ctx: Contexto) -> ir.MusicIR:
    nomes, alturas, dominios = _problema(ctx.parametros, ctx.plano)
    if valores == [SEM_SOLUCAO]:
        raise ValueError(f"a Situation não achou solução para as condições: {_condicoes(nomes, alturas, dominios)[0]}")
    _contar(valores, len(alturas))
    if estranhas := [v for v in valores if v not in alturas]:
        raise ValueError(f"a solução tem alturas que não foram pedidas: {estranhas[:5]}")
    k, acordes = len(nomes), math.ceil(len(alturas) / len(nomes))
    partes = []
    for j, nome in enumerate(nomes):
        inst = instrumento(nome)
        eventos: list[ir.Evento] = []
        for c in range(acordes):
            s, eid = c * k + j, f"P{j + 1}V1E{c + 1}"
            if s < len(valores):
                eventos.append(ir.Nota(id=eid, inicio=Fraction(c), duracao=Fraction(1), proveniencia=ctx.proveniencia,
                                       alturas=[ir.Altura(midicents=int(valores[s]))]))
            else:
                eventos.append(ir.Pausa(id=eid, inicio=Fraction(c), duracao=Fraction(1), proveniencia=ctx.proveniencia))
        partes.append(ir.Parte(id=f"P{j + 1}", instrumento=nome.strip().capitalize(), clave=inst.clave,
                               tessitura=inst.tessitura, vozes=[ir.Voz(id=f"P{j + 1}V1", eventos=eventos)]))
    solucao = ir.MusicIR(titulo="Distribuição por restrições (Situation)", compassos=_compassos(acordes),
                         partes=partes)
    violacoes = [str(v) for v in verificar(solucao, [Restricao(tipo="sem_unissono")])]
    vistos: dict[float, int] = {}
    for s, v in enumerate(valores):
        if v in vistos:
            violacoes.append(f"altura repetida ({nomes[s % k]} c.{s // k + 1}): {int(v)} já usada no "
                             f"{nomes[vistos[v] % k]} c.{vistos[v] // k + 1}")
        vistos.setdefault(v, s)
    if violacoes:
        raise ValueError(f"a solução importada viola restrições: {'; '.join(violacoes[:6])}")
    return solucao


# --- OMChroma: eventos da partitura -> síntese aditiva (add-1) -> Csound --------------------------------------------

def _hz(midicents: int) -> float:
    return 440.0 * 2 ** ((midicents - 6900) / 1200)


# shortcut: um andamento só (o primeiro da peça); converter compasso a compasso quando houver mudanças de andamento
def _segundos(atual: ir.MusicIR) -> float:
    """Segundos por semibreve."""
    bpm = next((c.andamento for c in atual.compassos if c.andamento), 60)
    return 4 * 60 / bpm


def _notas_selecionadas(atual: ir.MusicIR, plano: Plano | None) -> list[ir.Nota]:
    locais = ir.localizar(atual)
    return sorted((e for p, v, e in atual.eventos() if isinstance(e, ir.Nota)
                   and (plano is None or plano.selecao.contem(*locais[e.id]))), key=lambda e: e.inicio)


def _parciais(p: dict[str, float], inicio: Fraction, total: Fraction) -> int:
    """Parciais por nota no ponto `inicio` da peça: rampa do inicial ao final (densidade espectral crescente)."""
    t = float(inicio / total) if total else 0.0
    return max(1, round(p["parciais_inicial"] + (p["parciais_final"] - p["parciais_inicial"]) * t))


def componentes(atual: ir.MusicIR, plano: Plano | None, p: dict[str, float]) -> list[tuple[float, float, float, float]]:
    """(início s, duração s, frequência Hz, amplitude dB) de cada parcial: cada nota vira uma série de parciais
    harmônicos cuja quantidade sobe em rampa do início ao fim da peça (densidade espectral crescente)."""
    notas = _notas_selecionadas(atual, plano)
    if not notas:
        raise ValueError("a síntese precisa de notas na seleção")
    sps, total = _segundos(atual), sum(c.comprimento for c in atual.compassos)
    saida = []
    for n in notas:
        k = _parciais(p, n.inicio, total)
        for a in n.alturas:
            f0 = _hz(a.midicents)
            for j in range(1, k + 1):
                if f0 * j < 20000:
                    saida.append((float(n.inicio) * sps, float(n.duracao) * sps, f0 * j,
                                  p["amplitude"] - 6 * math.log2(j)))
    if len(saida) > LIMITE_COMPONENTES:
        raise ValueError(f"{len(saida)} parciais passam do limite de {LIMITE_COMPONENTES}; reduza parciais ou a seleção")
    return saida


def _chroma_montar(p: dict[str, float], atual: ir.MusicIR | None, plano: Plano | None) -> str:
    comps = componentes(exigir(atual), plano, p)
    return f"{len(comps)}\n" + "".join(f"{t:.6f} {d:.6f} {f:.6f} {a:.6f}\n" for t, d, f, a in comps)


def csound(which=shutil.which) -> str | None:
    """Executável do Csound: CSOUND (caminho) ou o PATH. O Registro usa o mesmo critério."""
    return os.environ.get("CSOUND") or which("csound")


def renderizar(pasta: Path, executavel: str | None, *, timeout: float = TIMEOUT_CSOUND) -> str:
    """muschat.orc + muschat.sco -> muschat.wav com o Csound detectado. Nunca levanta: devolve o que aconteceu."""
    orc, sco, wav = pasta / "muschat.orc", pasta / "muschat.sco", pasta / "muschat.wav"
    if not (orc.is_file() and sco.is_file()):
        return "áudio não gerado: o OMChroma não deixou muschat.orc/muschat.sco na pasta da tarefa"
    if executavel is None:
        return "áudio não gerado: Csound não encontrado (instale-o ou defina CSOUND)"
    try:
        # -d sem gráficos, -m0 sem mensagens por evento: a saída capturada fica pequena
        subprocess.run([executavel, "-d", "-m0", "-W", "-o", wav.name, orc.name, sco.name], cwd=pasta,
                       capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        return f"áudio não gerado: o Csound passou de {timeout:g} s"
    except (subprocess.CalledProcessError, OSError) as exc:
        detalhe = " ".join(str(getattr(exc, "stderr", "") or exc).strip().splitlines()[-2:])
        return f"áudio não gerado: o Csound falhou ({detalhe})"
    if not wav.is_file() or wav.stat().st_size == 0:
        return "áudio não gerado: o Csound não produziu muschat.wav"
    if wav.stat().st_size > LIMITE_AUDIO:
        wav.unlink()
        return f"áudio descartado: passou de {LIMITE_AUDIO // 1_000_000} MB"
    return f"áudio: muschat.wav na pasta da tarefa ({pasta.name})"


def _chroma_ler(valores: list[float], ctx: Contexto) -> ir.MusicIR:
    atual = exigir(ctx.atual)
    esperados = int(ctx.entrada.split(maxsplit=1)[0])
    if valores != [float(esperados)]:
        raise ValueError(f"a saída deveria ser o número de parciais sintetizados ({esperados}); veio {valores[:3]}")
    audio = renderizar(ctx.pasta, csound())
    notas = _notas_selecionadas(atual, ctx.plano)
    total = sum(c.comprimento for c in atual.compassos)
    p = ctx.parametros
    ids = Ids(atual)
    parte_id = ids.novo("ELE")
    cues: list[ir.Evento] = []
    for n, inicio in enumerate(sorted({n.inicio for n in notas})):
        juntas = [x for x in notas if x.inicio == inicio]
        descricao = (f"síntese aditiva: {len(juntas)} nota(s), até {_parciais(p, inicio, total)} parciais "
                     "(os acima de 20 kHz ficam de fora)")
        cues.append(ir.Eletronico(id=ids.novo(f"{parte_id}C"), inicio=inicio, duracao=Fraction(0),
                                  proveniencia=ctx.proveniencia, descricao=descricao + (f"; {audio}" if n == 0 else "")))
    curva = ir.Curva(id=ids.novo(f"{parte_id}K"), inicio=Fraction(0), duracao=total, proveniencia=ctx.proveniencia,
                     parametro="parciais por nota",
                     pontos=[(Fraction(0), p["parciais_inicial"]), (total, p["parciais_final"])])
    eletronica = ir.Parte(id=parte_id, instrumento="Eletrônica (síntese aditiva)", clave="percussao",
                          vozes=[ir.Voz(id=f"{parte_id}V1", eventos=cues), ir.Voz(id=f"{parte_id}V2", eventos=[curva])])
    return atual.model_copy(update={"partes": [*atual.partes, eletronica]})


# --- OM-SuperVP: análise de um som -> dado espectral ---------------------------------------------------------------

def _supervp_montar(p: dict[str, float], atual: ir.MusicIR | None, plano: Plano | None) -> str:
    return f"{int(p['parciais'])} {int(p['quadros'])}\n"


def _supervp_ler(valores: list[float], ctx: Contexto) -> ir.MusicIR:
    """N, depois N trincas (tempo s, frequência Hz, amplitude linear): cada tempo vira um evento espectral
    (1 s = uma semínima a 60, na grade de semicolcheias); nada vira nota."""
    if not valores or valores[0] != int(valores[0]) or len(valores) != 1 + 3 * int(valores[0]) or valores[0] < 1:
        raise ValueError("a análise deve ser N seguido de N trincas (tempo, frequência, amplitude)")
    trincas = [valores[1 + 3 * k: 4 + 3 * k] for k in range(int(valores[0]))]
    if ruins := [t for t in trincas if t[0] < 0 or not 0 < t[1] <= 30000 or t[2] < 0]:
        raise ValueError(f"trincas fora de faixa (tempo ≥ 0, 0 < Hz ≤ 30000, amplitude ≥ 0): {ruins[:3]}")
    quadros: dict[Fraction, list[ir.Parcial]] = {}
    for tempo, freq, amp in trincas:
        quadros.setdefault(Fraction(round(tempo * 4), 16), []).append(ir.Parcial(frequencia=freq, amplitude=amp))
    inicios = sorted(quadros)
    fins = [*inicios[1:], inicios[-1] + Fraction(1, 4)]
    eventos: list[ir.Evento] = [
        ir.Espectral(id=f"A1V1E{k + 1}", inicio=i, duracao=f - i, proveniencia=ctx.proveniencia,
                     parciais=sorted(quadros[i], key=lambda q: q.frequencia))
        for k, (i, f) in enumerate(zip(inicios, fins))]
    parte = ir.Parte(id="A1", instrumento="Análise espectral (OM-SuperVP)", vozes=[ir.Voz(id="A1V1", eventos=eventos)])
    return ir.MusicIR(titulo="Análise espectral (OM-SuperVP)", compassos=_compassos(max(1, math.ceil(fins[-1]))),
                      partes=[parte])


# --- catálogo --------------------------------------------------------------------------------------------------------

PATCHES: dict[str, PatchAprovado] = {p.procedimento: p for p in [
    PatchAprovado(
        procedimento="serie_harmonica", biblioteca="esquisse", funcao_om="harm-series",
        descricao="Série harmônica pela Esquisse (harm-series, unit = Midics): devolve alturas em midicents.",
        entrada="uma linha: fundamental_em_midicents numer denom begin end (ex.: 3600 1 1 1 16)",
        saida="saida.txt: a lista de midicents, separados por espaço (parênteses são aceitos)",
        passos=_passos("serie_harmonica.lisp", " Usa Midics (1) e Chord (1)."),
        montar=_serie_montar, ler=_serie_ler, autor="OpenMusic/Esquisse", arquivo="patches/serie_harmonica.lisp", validado=True),
    PatchAprovado(
        procedimento="densidade_caotica", biblioteca="chaos", funcao_om="Verhulst",
        descricao="Mapa logístico de Verhulst (Chaos): um valor por compasso da seleção, normalizado e usado como "
                  "densidade de ataques entre o mínimo e o máximo.",
        entrada="uma linha: x0 lambda iteracoes (iteracoes = compassos - 1)",
        saida="saida.txt: os valores da série (iteracoes + 1), separados por espaço",
        passos=_passos("densidade_caotica.lisp"),
        montar=_chaos_montar, ler=_chaos_ler, autor="OpenMusic/Chaos", arquivo="patches/densidade_caotica.lisp", validado=True),
    PatchAprovado(
        procedimento="distribuir_restricoes", biblioteca="situation", funcao_om="Csolver + generic-cnstr",
        descricao="Situation (Csolver com domínios por variável): cada vaga de cada instrumento recebe uma altura da "
                  "sua tessitura, todas diferentes (sem uníssono, cada altura uma vez).",
        entrada="N; depois uma linha por vaga: tamanho do domínio seguido das alturas possíveis (midicents)",
        saida="saida.txt: N alturas na ordem das vagas, ou -1 se não houver solução",
        passos=_passos("distribuir_restricoes.lisp"),
        montar=_situation_montar, ler=_situation_ler, autor="OpenMusic/Situation", arquivo="patches/distribuir_restricoes.lisp", validado=True),
    PatchAprovado(
        procedimento="sintese_aditiva", biblioteca="omchroma", funcao_om="add-1 + synthesize (:run nil)",
        descricao="OMChroma: cada nota vira parciais de síntese aditiva (classe add-1); o OMChroma escreve "
                  "muschat.orc/muschat.sco e o Muschat renderiza o áudio com o Csound detectado.",
        entrada="N; depois N linhas: início (s) duração (s) frequência (Hz) amplitude (dB)",
        saida="saida.txt: N (parciais escritos); muschat.orc e muschat.sco na pasta da tarefa",
        passos=_passos("sintese_aditiva.lisp", " Também grava muschat.orc e muschat.sco."),
        montar=_chroma_montar, ler=_chroma_ler, autor="OpenMusic/OMChroma", arquivo="patches/sintese_aditiva.lisp", validado=True),
    PatchAprovado(
        procedimento="analise_supervp", biblioteca="om-supervp", funcao_om="supervp (análise de parciais)",
        descricao="OM-SuperVP: análise de parciais de um som escolhido no OpenMusic (o arquivo de som não é "
                  "modificado); o resultado entra como dado espectral, nunca como nota.",
        entrada="uma linha: parciais quadros",
        saida="saida.txt: N seguido de N trincas tempo (s) frequência (Hz) amplitude",
        passos=_passos("analise_supervp.lisp"),
        montar=_supervp_montar, ler=_supervp_ler, autor="OpenMusic/OM-SuperVP"),  # sem arquivo: SuperVP não disponível para validar o patch
]}

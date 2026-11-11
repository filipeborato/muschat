"""Turno de composição: pedido + Composição -> Plano validado -> procedimento -> nova Versão (ou recusa explicada)."""

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.genai import types
from pydantic import ValidationError

from muschat import prompts
from muschat.composicao import Composicao, Versao
from muschat.musicir import MusicIR, Proveniencia, comparar, localizar
from muschat.notacao.musicxml import para_musicxml
from muschat.plano import Operacao, Plano
from muschat.procedimentos import CATALOGO, Procedimento
from muschat.seguranca import neutralizar

if TYPE_CHECKING:
    from muschat.reasoning import Gerador, SessaoChat


@dataclass(frozen=True)
class Resultado:
    resumo: str  # o que foi feito, ou por que nada foi feito (vai para a síntese)
    versao: Versao | None  # None = recusa: a Composição não mudou


class Recusa(Exception):
    pass


def compor(
    pedido: str,
    composicao: Composicao,
    *,
    gerador: "Gerador",
    chat: "SessaoChat",
    modelo: str,
    catalogo: dict[str, Procedimento] = CATALOGO,
) -> Resultado:
    atual = composicao.atual.ir if composicao.atual else None
    try:
        plano = _planejar(pedido, atual, gerador=gerador, chat=chat, modelo=modelo, catalogo=catalogo)
        if plano.motor == "openmusic":
            raise Recusa("o motor OpenMusic ainda não está integrado; peça com o motor Python ou automático.")
        parametros = [_parametros(op, catalogo, atual) for op in plano.operacoes]
        _validar_selecao(plano, atual)
        semente = plano.semente if plano.semente is not None else random.randrange(2**31)
        operacao = f"op{len(composicao.versoes) + 1}"
        nova = _executar(plano, parametros, semente, operacao, atual, catalogo)
    except Recusa as exc:
        return Resultado(f"Operação recusada; a composição não mudou. Motivo: {exc}", None)
    passos = ", ".join(f"{op.procedimento}({_texto(p, catalogo[op.procedimento])})"
                       for op, p in zip(plano.operacoes, parametros))
    resumo = (f"Versão {len(composicao.versoes) + 1}: {passos}, semente {semente}, motor Python; "
              f"{len(nova.partes)} partes, {len(nova.compassos)} compassos"
              + ("" if plano.selecao.total else f"; seleção {plano.selecao.model_dump(exclude_defaults=True)}"))
    versao = composicao.adicionar(nova, resumo, {
        "pedido": pedido, "modelo": modelo, "plano": plano.model_dump(), "operacao": operacao,
        "procedimentos": [op.procedimento for op in plano.operacoes], "parametros": parametros, "semente": semente,
    })
    return Resultado(resumo, versao)


def _planejar(pedido: str, atual: MusicIR | None, *, gerador: "Gerador", chat: "SessaoChat", modelo: str,
              catalogo: dict[str, Procedimento]) -> Plano:
    texto = (f"<pergunta_usuario>\n{neutralizar(pedido)}\n</pergunta_usuario>\n"
             f"<composicao_atual>\n{resumir(atual)}\n</composicao_atual>")
    resposta = gerador.generate_content(
        model=modelo,
        contents=[*chat.get_history(), types.Content(role="user", parts=[types.Part(text=texto)])],
        config=types.GenerateContentConfig(
            system_instruction=prompts.PLANO.format(catalogo=_catalogo_no_prompt(catalogo)),
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=Plano,
        ),
    )
    try:
        return Plano.model_validate_json(resposta.text or "")
    except ValidationError as exc:
        raise Recusa("o plano composicional devolvido pelo modelo não é válido.") from exc


def _parametros(op: Operacao, catalogo: dict[str, Procedimento], atual: MusicIR | None) -> dict[str, float]:
    nome, dados = op.procedimento, {p.nome: p.valor for p in op.parametros}
    if len(dados) < len(op.parametros):
        raise Recusa(f"{nome}: parâmetro repetido no plano.")
    if nome not in catalogo:
        raise Recusa(f"o procedimento {nome!r} não existe no catálogo ({', '.join(catalogo)}).")
    proc = catalogo[nome]
    if proc.requer_composicao and atual is None:
        raise Recusa(f"{nome} transforma uma composição, e ainda não há nenhuma nesta conversa.")
    if desconhecidos := dados.keys() - proc.parametros.keys():
        raise Recusa(f"{nome} não tem os parâmetros {sorted(desconhecidos)}.")
    for chave, valor in dados.items():
        faixa = proc.parametros[chave]
        if not faixa.minimo <= valor <= faixa.maximo or (faixa.inteiro and valor != int(valor)):
            tipo = "inteiro " if faixa.inteiro else ""
            raise Recusa(f"{nome}: {chave}={valor:g} fora da faixa ({tipo}{faixa.minimo:g} a {faixa.maximo:g}).")
    return {chave: dados.get(chave, faixa.padrao) for chave, faixa in proc.parametros.items()}


def _validar_selecao(plano: Plano, atual: MusicIR | None) -> None:
    sel = plano.selecao
    if sel.total:
        return
    if atual is None:
        raise Recusa("a seleção aponta para uma composição que ainda não existe.")
    partes = {p.id for p in atual.partes}
    vozes = {v.id for p in atual.partes for v in p.vozes}
    if inexistentes := [i for i in sel.partes if i not in partes] + [i for i in sel.vozes if i not in vozes]:
        raise Recusa(f"a seleção cita ids que não existem na composição: {inexistentes}.")
    ini, fim = sel.compasso_inicial or 1, sel.compasso_final or len(atual.compassos)
    if not 1 <= ini <= fim <= len(atual.compassos):
        raise Recusa(f"compassos {ini}–{fim} fora da composição (1–{len(atual.compassos)}).")


def _executar(plano: Plano, parametros: list[dict[str, float]], semente: int, operacao: str,
              atual: MusicIR | None, catalogo: dict[str, Procedimento]) -> MusicIR:
    rng = random.Random(semente)
    nova = atual
    try:
        for op, params in zip(plano.operacoes, parametros):
            nova = catalogo[op.procedimento].funcao(nova, plano, params, rng)
        if nova is None:
            raise ValueError("o plano não produziu composição")
        nova = MusicIR.model_validate(nova.model_dump())  # model_copy dos procedimentos não revalida
        para_musicxml(nova)  # a Versão precisa ser notável (ex.: nada atravessa a barra de compasso)
    except ValueError as exc:  # inclui ValidationError
        raise Recusa((str(exc) or type(exc).__name__).splitlines()[0]) from exc
    if atual is None:
        return _carimbar(nova, {e.id for _, _, e in nova.eventos()}, operacao)
    dif = comparar(atual, nova)
    antes, depois = localizar(atual), localizar(nova)
    fora = sorted({i for i in [*dif.adicionados, *dif.removidos, *dif.alterados]
                   for local in (antes.get(i), depois.get(i)) if local and not plano.selecao.contem(*local)})
    cabecalho = (atual.titulo, atual.compositor) != (nova.titulo, nova.compositor)
    if fora or ((dif.compassos or cabecalho) and not plano.selecao.total):
        onde = fora or (f"compassos {dif.compassos}" if dif.compassos else "título/compositor")
        raise Recusa(f"o procedimento alterou material fora da seleção: {onde}.")
    return _carimbar(nova, set(dif.adicionados) | set(dif.alterados), operacao)


def _carimbar(ir: MusicIR, ids: set[str], operacao: str) -> MusicIR:
    """Proveniência `algoritmo` + id da operação nos eventos criados ou alterados."""
    prov = Proveniencia(origem="algoritmo", operacao=operacao)
    return ir.model_copy(update={"partes": [p.model_copy(update={"vozes": [v.model_copy(update={"eventos": [
        e.model_copy(update={"proveniencia": prov}) if e.id in ids else e for e in v.eventos
    ]}) for v in p.vozes]}) for p in ir.partes]})


def resumir(ir: MusicIR | None) -> str:
    """Resumo da composição para o prompt: ids que a seleção pode citar."""
    if ir is None:
        return "nenhuma composição ainda"
    metricas = sorted({f"{c.numerador}/{c.denominador}" for c in ir.compassos})
    linhas = [f"título: {ir.titulo}", f"compassos: {len(ir.compassos)} ({', '.join(metricas)})", "partes:"]
    for p in ir.partes:
        vozes = ", ".join(f"{v.id} ({len(v.eventos)} eventos)" for v in p.vozes)
        linhas.append(f"- {p.id}: {p.instrumento}; vozes {vozes}")
    return neutralizar("\n".join(linhas))


def _catalogo_no_prompt(catalogo: dict[str, Procedimento]) -> str:
    linhas = []
    for proc in catalogo.values():
        linhas.append(f"- {proc.nome}: {proc.descricao}")
        for nome, f in proc.parametros.items():
            tipo = "inteiro" if f.inteiro else "real"
            linhas.append(f"    - {nome} ({tipo} {f.unidade}, {f.minimo:g} a {f.maximo:g}, padrão {f.padrao:g}): "
                          f"{f.descricao}")
    return "\n".join(linhas)


def _texto(parametros: dict[str, float], proc: Procedimento) -> str:
    return ", ".join(f"{k}={v:g} {proc.parametros[k].unidade}".rstrip() for k, v in parametros.items())

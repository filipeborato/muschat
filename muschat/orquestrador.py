"""Turno de composição: pedido + Composição + modo -> Plano validado -> roteador -> lista de etapas -> nova Versão,
ou etapa assistida aguardando o compositor, ou recusa explicada.

Cada etapa roda em ordem; depois de cada uma a MusicIR é revalidada, o invariante de preservação é conferido contra a
etapa anterior e os eventos criados/alterados ganham a proveniência do motor. Uma etapa do OpenMusic pausa a lista
(Pendência na Composição); `retomar` importa a saída e segue dali. Falha em qualquer etapa: nenhuma Versão parcial.
"""

import platform
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from google.genai import types
from pydantic import ValidationError

from muschat import prompts
from muschat.composicao import Composicao, Pendencia, Versao
from muschat.instrumentos import FAMILIAS, INSTRUMENTOS
from muschat.musicir import MusicIR, Origem, Proveniencia, comparar, localizar
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.tecnicas import tecnicas_da_familia
from muschat.openmusic import PASTA_TAREFAS, Tarefa, criar_tarefa, importar
from muschat.plano import Operacao, Parametro, Plano
from muschat.procedimentos import CATALOGO, Procedimento
from muschat.registro import NOME_DO_MOTOR, Registro
from muschat.restricoes import verificar
from muschat.roteador import MODOS, Modo, SemRota, rotear
from muschat.seguranca import neutralizar

if TYPE_CHECKING:
    from muschat.reasoning import Gerador, SessaoChat


@dataclass(frozen=True)
class Resultado:
    resumo: str  # o que foi feito, o que espera o compositor, ou por que nada foi feito (vai para a síntese)
    versao: Versao | None  # None = nenhuma Versão nova (recusa ou etapa aguardando)
    tarefa: Tarefa | None = None  # etapa assistida aguardando o compositor


class Recusa(Exception):
    pass


def compor(
    pedido: str,
    composicao: Composicao,
    *,
    gerador: "Gerador",
    chat: "SessaoChat",
    modelo: str,
    modo: Modo = "automatico",
    registro: Registro | None = None,
    catalogo: dict[str, Procedimento] = CATALOGO,
    pasta_tarefas: Path = PASTA_TAREFAS,
) -> Resultado:
    registro = registro or Registro.so_python(_nativos(catalogo))
    if composicao.pendente:
        return _aguardando(composicao.pendente)
    atual = composicao.atual.ir if composicao.atual else None
    try:
        plano = _planejar(pedido, atual, gerador=gerador, chat=chat, modelo=modelo, catalogo=catalogo,
                          registro=registro)
    except Recusa as exc:
        return _recusa(exc)
    return _aplicar(plano, composicao, composicao.indice, pedido=pedido, modelo=modelo, modo=modo,
                    registro=registro, catalogo=catalogo, pasta=pasta_tarefas)


def reaplicar(composicao: Composicao, parametros: list[dict[str, float]], *, registro: Registro | None = None,
              catalogo: dict[str, Procedimento] = CATALOGO, pasta_tarefas: Path = PASTA_TAREFAS) -> Resultado:
    """Refaz a operação da Versão atual, a partir da mesma Versão de origem, com a mesma semente e o mesmo modo,
    trocando só os parâmetros (o compositor ajustou a hipótese operacional). Não chama o modelo."""
    versao = composicao.atual
    if versao is None:
        return _recusa(Recusa("não há operação para reaplicar."))
    if composicao.pendente:
        return _aguardando(composicao.pendente)
    plano = Plano.model_validate(versao.experimento["plano"])
    plano = plano.model_copy(update={"semente": versao.experimento["semente"], "operacoes": [
        op.model_copy(update={"parametros": [Parametro(nome=k, valor=v) for k, v in novos.items()]})
        for op, novos in zip(plano.operacoes, parametros, strict=True)]})
    return _aplicar(plano, composicao, versao.anterior, pedido=f"{versao.experimento['pedido']} (reaplicado)",
                    modelo=versao.experimento["modelo"], modo=versao.experimento.get("modo", "automatico"),
                    registro=registro or Registro.so_python(_nativos(catalogo)), catalogo=catalogo,
                    pasta=pasta_tarefas)


def retomar(composicao: Composicao, saida: bytes | str, *, catalogo: dict[str, Procedimento] = CATALOGO,
            pasta_tarefas: Path = PASTA_TAREFAS) -> Resultado:
    """Importa a saída da etapa do OpenMusic que espera o compositor e segue a lista de etapas dali."""
    p = composicao.pendente
    if p is None or p.tarefa is None:
        return _recusa(Recusa("nenhuma etapa do OpenMusic está aguardando."))
    try:
        material = importar(p.tarefa, saida, operacao=p.operacao, atual=p.atual, plano=p.plano,
                            rng=random.Random(f"{p.semente}/{p.etapa}"))
    except ValueError as exc:  # a tarefa continua aguardando: o compositor pode reenviar
        return Resultado(f"A saída do OpenMusic foi recusada e a tarefa continua aguardando você: {exc}.", None,
                         p.tarefa)
    composicao.pendente = None  # daqui em diante, sucesso grava a Versão e falha descarta a lista inteira
    try:
        nova = _conferir(p.plano, p.atual, material, origem="openmusic", operacao=p.operacao)
        contexto = p.model_copy(update={"etapa": p.etapa + 1, "tarefa": None,
                                        "intermediarios": [*p.intermediarios, nova]})
        return _seguir(contexto, nova, composicao, catalogo, pasta_tarefas)
    except Recusa as exc:
        return _recusa(exc)


def descartar(composicao: Composicao) -> None:
    """Abandona a etapa que espera o compositor e apaga a pasta de trabalho da tarefa."""
    if composicao.pendente and composicao.pendente.tarefa:
        shutil.rmtree(composicao.pendente.tarefa.pasta, ignore_errors=True)
    composicao.pendente = None


def _recusa(exc: Exception) -> Resultado:
    return Resultado(f"Operação recusada; a composição não mudou. Motivo: {exc}", None)


def _aguardando(p: Pendencia) -> Resultado:
    etapa = p.decisao.etapas[p.etapa]
    return Resultado(f"Aguardando você: a etapa {p.etapa + 1} de {len(p.decisao.etapas)} ({etapa.procedimento} no "
                     "OpenMusic) precisa da saída do patch antes de qualquer outra operação; envie o arquivo ou "
                     "descarte a tarefa.", None, p.tarefa)


def _aplicar(plano: Plano, composicao: Composicao, base: int | None, *, pedido: str, modelo: str, modo: Modo,
             registro: Registro, catalogo: dict[str, Procedimento], pasta: Path) -> Resultado:
    atual = None if base is None else composicao.versoes[base].ir
    try:
        try:
            decisao = rotear(plano, modo, registro)
        except SemRota as exc:
            raise Recusa(str(exc)) from exc
        parametros, existe = [], atual is not None
        for op in plano.operacoes:  # uma operação que cria peça vale para as seguintes do mesmo plano
            parametros.append(_parametros(op, catalogo, existe))
            existe = existe or not catalogo[op.procedimento].requer_composicao
        _validar_selecao(plano, atual)
        contexto = Pendencia(
            pedido=pedido, modelo=modelo, plano=plano, decisao=decisao, parametros=parametros,
            semente=plano.semente if plano.semente is not None else random.randrange(2**31),
            operacao=f"op{len(composicao.versoes) + 1}", base=base, etapa=0, atual=atual, intermediarios=[])
        return _seguir(contexto, atual, composicao, catalogo, pasta)
    except Recusa as exc:
        return _recusa(exc)


def _seguir(ctx: Pendencia, atual: MusicIR | None, composicao: Composicao, catalogo: dict[str, Procedimento],
            pasta: Path) -> Resultado:
    intermediarios = list(ctx.intermediarios)
    for k in range(ctx.etapa, len(ctx.decisao.etapas)):
        etapa = ctx.decisao.etapas[k]
        if etapa.motor == "openmusic":
            try:
                tarefa = criar_tarefa(etapa.procedimento, ctx.parametros[k], raiz=pasta, atual=atual,
                                      plano=ctx.plano)
            except OSError as exc:
                raise Recusa(f"não foi possível criar a pasta da tarefa do OpenMusic ({exc})") from exc
            except ValueError as exc:  # a entrada do patch não se monta (ex.: seleção sem notas)
                raise Recusa(f"etapa {k + 1} ({etapa.procedimento}): {_primeira_linha(exc)}") from exc
            composicao.pendente = ctx.model_copy(update={"etapa": k, "atual": atual, "tarefa": tarefa,
                                                         "intermediarios": intermediarios})
            return Resultado(f"Aguardando você: etapa {k + 1} de {len(ctx.decisao.etapas)}, {etapa.procedimento} no "
                             f"OpenMusic (assistido). Baixe a tarefa {tarefa.id}, rode o patch e envie saida.txt. "
                             f"Escolha do motor: {' '.join(ctx.decisao.justificativa)}", None, tarefa)
        rng = random.Random(f"{ctx.semente}/{k}")  # uma semente por etapa: retomar depois de uma pausa reproduz
        try:
            nova = catalogo[etapa.procedimento].funcao(atual, ctx.plano, ctx.parametros[k], rng)
        except ValueError as exc:  # inclui ValidationError
            raise Recusa(f"etapa {k + 1} ({etapa.procedimento}): {_primeira_linha(exc)}") from exc
        atual = _conferir(ctx.plano, atual, nova, origem="algoritmo", operacao=ctx.operacao)
        intermediarios.append(atual)
    if atual is None:
        raise Recusa("o plano não produziu composição")
    return _gravar(ctx.model_copy(update={"intermediarios": intermediarios}), atual, composicao, catalogo)


def _gravar(ctx: Pendencia, nova: MusicIR, composicao: Composicao, catalogo: dict[str, Procedimento]) -> Resultado:
    violacoes = [str(v) for v in verificar(nova, ctx.plano.restricoes)]
    passos = ", ".join(f"{e.procedimento}[{NOME_DO_MOTOR[e.motor]}]({_texto(p, catalogo[e.procedimento])})"
                       for e, p in zip(ctx.decisao.etapas, ctx.parametros))
    plano = ctx.plano
    resumo = (f"Versão {len(composicao.versoes) + 1}: {passos}, semente {ctx.semente}, modo "
              f"{MODOS[ctx.decisao.modo]}; {len(nova.partes)} partes, {len(nova.compassos)} compassos"
              + ("" if plano.selecao.total else f"; seleção {plano.selecao.model_dump(exclude_defaults=True)}")
              + "".join(f"; hipótese: {neutralizar(h)}" for h in plano.hipoteses)
              + (f"; restrições violadas ({len(violacoes)}): {'; '.join(violacoes[:5])}" if violacoes
                 else "; restrições cumpridas")
              + f". Escolha do motor: {' '.join(ctx.decisao.justificativa)}")
    versao = composicao.adicionar(nova, resumo, {
        "pedido": ctx.pedido, "modelo": ctx.modelo, "modo": ctx.decisao.modo, "plano": plano.model_dump(mode="json"),
        "decisao": ctx.decisao.model_dump(mode="json"), "operacao": ctx.operacao,
        "procedimentos": [op.procedimento for op in plano.operacoes], "parametros": ctx.parametros,
        "semente": ctx.semente, "violacoes": violacoes, "dependencias": dependencias(),
        "etapas": [{**e.model_dump(), "estado": "feita"} for e in ctx.decisao.etapas],
        "intermediarios": [ir.model_dump(mode="json") for ir in ctx.intermediarios],
    }, anterior=ctx.base)
    return Resultado(resumo, versao)


def dependencias() -> dict[str, str | None]:
    """Versões das dependências Python que produzem e escrevem a música (proveniência de cada Versão)."""
    def v(pacote: str) -> str | None:
        try:
            return version(pacote)
        except PackageNotFoundError:
            return None

    return {"python": platform.python_version(), **{p: v(p) for p in ("abjad", "verovio", "google-genai")}}


def _conferir(plano: Plano, antes: MusicIR | None, nova: MusicIR, *, origem: Origem, operacao: str) -> MusicIR:
    """Revalida a MusicIR da etapa, confere o invariante de preservação contra a etapa anterior e carimba a
    proveniência nos eventos criados/alterados."""
    try:
        nova = MusicIR.model_validate(nova.model_dump())  # model_copy dos procedimentos não revalida
        para_musicxml(nova)  # cada etapa precisa ser notável (ex.: nada atravessa a barra de compasso)
    except ValueError as exc:
        raise Recusa(_primeira_linha(exc)) from exc
    if antes is None:
        return _carimbar(nova, {e.id for _, _, e in nova.eventos()}, origem, operacao)
    dif = comparar(antes, nova)
    la, ld = localizar(antes), localizar(nova)
    fora = sorted({i for i in [*dif.adicionados, *dif.removidos, *dif.alterados]
                   for local in (la.get(i), ld.get(i)) if local and not plano.selecao.contem(*local)})
    cabecalho = (antes.titulo, antes.compositor) != (nova.titulo, nova.compositor)
    if fora or ((dif.compassos or cabecalho) and not plano.selecao.total):
        onde = fora or (f"compassos {dif.compassos}" if dif.compassos else "título/compositor")
        raise Recusa(f"o procedimento alterou material fora da seleção: {onde}.")
    return _carimbar(nova, set(dif.adicionados) | set(dif.alterados), origem, operacao)


def _carimbar(ir: MusicIR, ids: set[str], origem: Origem, operacao: str) -> MusicIR:
    """Proveniência do motor + id da operação nos eventos criados ou alterados (o autor do OpenMusic fica)."""
    def prov(atual: Proveniencia) -> Proveniencia:
        return Proveniencia(origem=origem, operacao=operacao, autor=atual.autor if atual.origem == origem else None)

    return ir.model_copy(update={"partes": [p.model_copy(update={"vozes": [v.model_copy(update={"eventos": [
        e.model_copy(update={"proveniencia": prov(e.proveniencia)}) if e.id in ids else e for e in v.eventos
    ]}) for v in p.vozes]}) for p in ir.partes]})


def _nativos(catalogo: dict[str, Procedimento]) -> tuple[str, ...]:
    return tuple(n for n, p in catalogo.items() if p.funcao is not None)


def _primeira_linha(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]


def _planejar(pedido: str, atual: MusicIR | None, *, gerador: "Gerador", chat: "SessaoChat", modelo: str,
              catalogo: dict[str, Procedimento], registro: Registro) -> Plano:
    texto = (f"<pergunta_usuario>\n{neutralizar(pedido)}\n</pergunta_usuario>\n"
             f"<composicao_atual>\n{resumir(atual)}\n</composicao_atual>")
    resposta = gerador.generate_content(
        model=modelo,
        contents=[*chat.get_history(), types.Content(role="user", parts=[types.Part(text=texto)])],
        config=types.GenerateContentConfig(
            system_instruction=prompts.PLANO.format(
                catalogo=_catalogo_no_prompt(catalogo, registro),
                instrumentos="; ".join(f"{f}: {', '.join(n for n, i in INSTRUMENTOS.items() if i.familia == f)}"
                                       for f in FAMILIAS),
                tecnicas="; ".join(f"{f}: {', '.join(tecnicas_da_familia(f))}" for f in FAMILIAS),
            ),
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=Plano,
        ),
    )
    try:
        return Plano.model_validate_json(resposta.text or "")
    except ValidationError as exc:
        raise Recusa("o plano composicional devolvido pelo modelo não é válido.") from exc


def _parametros(op: Operacao, catalogo: dict[str, Procedimento], existe: bool) -> dict[str, float]:
    nome, dados = op.procedimento, {p.nome: p.valor for p in op.parametros}
    if len(dados) < len(op.parametros):
        raise Recusa(f"{nome}: parâmetro repetido no plano.")
    if nome not in catalogo:
        raise Recusa(f"o procedimento {nome!r} não existe no catálogo ({', '.join(catalogo)}).")
    proc = catalogo[nome]
    if proc.requer_composicao and not existe:
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
    if sel.total or atual is None:  # peça nova: a seleção vale sobre o que o plano acabou de criar
        return
    partes = {p.id for p in atual.partes}
    vozes = {v.id for p in atual.partes for v in p.vozes}
    if inexistentes := [i for i in sel.partes if i not in partes] + [i for i in sel.vozes if i not in vozes]:
        raise Recusa(f"a seleção cita ids que não existem na composição: {inexistentes}.")
    ini, fim = sel.compasso_inicial or 1, sel.compasso_final or len(atual.compassos)
    if not 1 <= ini <= fim <= len(atual.compassos):
        raise Recusa(f"compassos {ini}–{fim} fora da composição (1–{len(atual.compassos)}).")


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


def _catalogo_no_prompt(catalogo: dict[str, Procedimento], registro: Registro) -> str:
    """Só o que o Registro diz que roda nesta máquina (nada planejado ou não instalado entra no prompt)."""
    linhas = []
    for nome in registro.disponiveis():
        if nome not in catalogo:
            continue
        proc = catalogo[nome]
        motores = registro.implementacoes(nome)
        onde = " Também no OpenMusic (assistido: o compositor roda o patch)." if "openmusic" in motores else ""
        linhas.append(f"- {proc.nome}: {proc.descricao}{onde}")
        for chave, f in proc.parametros.items():
            tipo = "inteiro" if f.inteiro else "real"
            linhas.append(f"    - {chave} ({tipo} {f.unidade}, {f.minimo:g} a {f.maximo:g}, padrão {f.padrao:g}): "
                          f"{f.descricao}")
    return "\n".join(linhas)


def _texto(parametros: dict[str, float], proc: Procedimento) -> str:
    return ", ".join(f"{k}={v:g} {proc.parametros[k].unidade}".rstrip() for k, v in parametros.items())

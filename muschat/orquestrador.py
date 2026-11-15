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
from muschat.instrumentos import FAMILIAS, INSTRUMENTOS, instrumento
from muschat.musicir import MusicIR, Origem, Parte, Proveniencia, comparar, localizar
from muschat.notagen import Compositor
from muschat.notacao.musicxml import de_musicxml, para_musicxml
from muschat.notacao.tecnicas import tecnicas_da_familia, tecnicas_intocaveis
from muschat.openmusic import PASTA_TAREFAS, Tarefa, criar_tarefa, importar
from muschat.plano import Operacao, Parametro, Plano
from muschat.procedimentos import CATALOGO, Procedimento
from muschat.registro import NOME_DO_MOTOR, Registro
from muschat.restricoes import verificar
from muschat.roteador import MODOS, Etapa, Modo, SemRota, rotear
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
    compositor: Compositor | None = None,
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
                    registro=registro, catalogo=catalogo, pasta=pasta_tarefas, compositor=compositor)


def reaplicar(composicao: Composicao, parametros: list[dict[str, float]], *, registro: Registro | None = None,
              catalogo: dict[str, Procedimento] = CATALOGO, pasta_tarefas: Path = PASTA_TAREFAS,
              compositor: Compositor | None = None) -> Resultado:
    """Refaz a operação da Versão atual, a partir da mesma Versão de origem, com a mesma semente e o mesmo modo,
    trocando só os parâmetros (o compositor ajustou a hipótese operacional). Não chama o modelo."""
    versao = composicao.atual
    if versao is None:
        return _recusa(Recusa("não há operação para reaplicar."))
    if composicao.pendente:
        return _aguardando(composicao.pendente)
    if not versao.reaplicavel:
        return _recusa(Recusa("esta Versão veio de um arquivo aberto; não há operação para reaplicar."))
    plano = Plano.model_validate(versao.experimento["plano"])
    plano = plano.model_copy(update={"semente": versao.experimento["semente"], "operacoes": [
        op.model_copy(update={"parametros": [Parametro(nome=k, valor=v) for k, v in novos.items()]})
        for op, novos in zip(plano.operacoes, parametros, strict=True)]})
    # saída do NotaGen com os mesmos parâmetros é reusada (o modelo não reproduz por semente com garantia)
    reuso = {int(k): xml for k, xml in versao.experimento.get("saidas_externas", {}).items()
             if parametros[int(k)] == versao.experimento["parametros"][int(k)]}
    return _aplicar(plano, composicao, versao.anterior, pedido=f"{versao.experimento['pedido']} (reaplicado)",
                    modelo=versao.experimento["modelo"], modo=versao.experimento.get("modo", "automatico"),
                    registro=registro or Registro.so_python(_nativos(catalogo)), catalogo=catalogo,
                    pasta=pasta_tarefas, compositor=compositor, reuso=reuso)


def retomar(composicao: Composicao, saida: bytes | str, *, catalogo: dict[str, Procedimento] = CATALOGO,
            pasta_tarefas: Path = PASTA_TAREFAS, compositor: Compositor | None = None) -> Resultado:
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
        return _seguir(contexto, nova, composicao, catalogo, pasta_tarefas, compositor)
    except Recusa as exc:
        return _recusa(exc)


def abrir(composicao: Composicao, dados: bytes, nome: str, *, origem: Origem = "compositor",
          autor: str | None = None) -> Resultado:
    """Um arquivo MusicXML (.musicxml, .xml ou .mxl) vira Versão nova, com a proveniência de quem o fez (o
    compositor, ou um motor externo com autor); o que a MusicIR não representa vai como aviso no resumo. Desfazer
    volta à Versão de antes."""
    if composicao.pendente:
        return _aguardando(composicao.pendente)
    nome = neutralizar(nome)
    try:
        ir, avisos = de_musicxml(dados)
        operacao = f"op{len(composicao.versoes) + 1}"
        ir = _validar(ir)
        ir = _carimbar(ir, {e.id for _, _, e in ir.eventos()}, origem, operacao, autor)
    except (ValueError, Recusa) as exc:
        return _recusa(Recusa(f"{nome}: {_primeira_linha(exc)}"))
    avisos = [neutralizar(a) for a in avisos]
    resumo = (f"Versão {len(composicao.versoes) + 1}: aberto {nome}; {len(ir.partes)} partes, {len(ir.compassos)} "
              f"compassos" + (f"; descartado na leitura: {'; '.join(avisos)}" if avisos else ""))
    versao = composicao.adicionar(ir, resumo, {
        "pedido": f"abrir {nome}", "operacao": operacao, "arquivo": nome, "avisos": avisos,
        "dependencias": dependencias()}, anterior=composicao.indice)
    return Resultado(resumo, versao)


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
             registro: Registro, catalogo: dict[str, Procedimento], pasta: Path, compositor: Compositor | None,
             reuso: dict[int, str] | None = None) -> Resultado:
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
        _validar_condicoes(plano, decisao.etapas, registro)
        contexto = Pendencia(
            pedido=pedido, modelo=modelo, plano=plano, decisao=decisao, parametros=parametros,
            semente=plano.semente if plano.semente is not None else random.randrange(2**31),
            operacao=f"op{len(composicao.versoes) + 1}", base=base, etapa=0, atual=atual, intermediarios=[],
            saidas_externas=reuso or {})
        return _seguir(contexto, atual, composicao, catalogo, pasta, compositor)
    except Recusa as exc:
        return _recusa(exc)


def _seguir(ctx: Pendencia, atual: MusicIR | None, composicao: Composicao, catalogo: dict[str, Procedimento],
            pasta: Path, compositor: Compositor | None) -> Resultado:
    intermediarios = list(ctx.intermediarios)
    for k in range(ctx.etapa, len(ctx.decisao.etapas)):
        etapa = ctx.decisao.etapas[k]
        if etapa.motor == "notagen":
            atual, ctx = _etapa_notagen(ctx, k, atual, compositor)
            intermediarios.append(atual)
            continue
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
    violacoes += [f"técnica intocável: {t}" for t in tecnicas_intocaveis(nova)]
    passos = ", ".join(f"{e.procedimento}[{NOME_DO_MOTOR[e.motor]}]({_texto(p, catalogo[e.procedimento])})"
                       for e, p in zip(ctx.decisao.etapas, ctx.parametros))
    plano = ctx.plano
    resumo = (f"Versão {len(composicao.versoes) + 1}: {passos}, semente {ctx.semente}, modo "
              f"{MODOS[ctx.decisao.modo]}; {len(nova.partes)} partes, {len(nova.compassos)} compassos"
              + ("" if plano.selecao.total else f"; seleção {plano.selecao.model_dump(exclude_defaults=True)}")
              + "".join(f"; hipótese: {neutralizar(h)}" for h in plano.hipoteses)
              + (f"; restrições violadas ({len(violacoes)}): {'; '.join(violacoes[:5])}" if violacoes
                 else "; restrições cumpridas")
              + (f"; descartado na leitura: {'; '.join(ctx.avisos)}" if ctx.avisos else "")
              + f". Escolha do motor: {' '.join(ctx.decisao.justificativa)}")
    versao = composicao.adicionar(nova, resumo, {
        "pedido": ctx.pedido, "modelo": ctx.modelo, "modo": ctx.decisao.modo, "plano": plano.model_dump(mode="json"),
        "decisao": ctx.decisao.model_dump(mode="json"), "operacao": ctx.operacao,
        "procedimentos": [op.procedimento for op in plano.operacoes], "parametros": ctx.parametros,
        "semente": ctx.semente, "violacoes": violacoes, "dependencias": dependencias(),
        "etapas": [{**e.model_dump(), "estado": "feita"} for e in ctx.decisao.etapas],
        "intermediarios": [ir.model_dump(mode="json") for ir in ctx.intermediarios],
        "saidas_externas": {str(k): xml for k, xml in ctx.saidas_externas.items()}, "avisos": ctx.avisos,
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
    nova = _validar(nova)
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


def _validar_condicoes(plano: Plano, etapas: list[Etapa], registro: Registro) -> None:
    """Condição do NotaGen conferida contra as que ele aceita antes de rodar qualquer etapa."""
    cap = registro.capacidade("NotaGen")
    for op, etapa in zip(plano.operacoes, etapas):
        if etapa.motor == "notagen" and (cap is None or op.condicao not in cap.opcoes):
            raise Recusa(f"{op.procedimento}: condição {op.condicao!r} não é aceita pelo NotaGen-X.")


def _etapa_notagen(ctx: Pendencia, k: int, atual: MusicIR | None,
                   compositor: Compositor | None) -> tuple[MusicIR, Pendencia]:
    """Peça nova do NotaGen (ou a saída guardada, ao reaplicar), lida como MusicXML, com as partes renomeadas para
    os instrumentos do plano quando a quantidade bate."""
    op = ctx.plano.operacoes[k]
    xml = ctx.saidas_externas.get(k)
    if xml is None:
        if compositor is None:
            raise Recusa("o NotaGen não está ligado nesta sessão (MUSCHAT_NOTAGEN=1; ver README).")
        try:
            xml = compositor.gerar(op.condicao, int(ctx.parametros[k]["compassos"]),
                                   random.Random(f"{ctx.semente}/{k}").randrange(2**31)).decode("utf-8")
        except ValueError as exc:  # inclui UnicodeDecodeError
            raise Recusa(f"etapa {k + 1} ({op.procedimento}): {_primeira_linha(exc)}") from exc
    try:
        ir, avisos = de_musicxml(xml.encode("utf-8"), Proveniencia(origem="externo", autor="NotaGen-X"))
    except ValueError as exc:
        raise Recusa(f"etapa {k + 1} ({op.procedimento}): saída ilegível: {_primeira_linha(exc)}") from exc
    pedidos = int(ctx.parametros[k]["compassos"])
    if len(ir.compassos) < pedidos:  # a peça do modelo acabou antes
        avisos.append(f"o NotaGen escreveu {len(ir.compassos)} de {pedidos} compassos")
    if len(ctx.plano.instrumentos) == len(ir.partes):
        ir = ir.model_copy(update={"partes": [_com_instrumento(p, nome)
                                              for p, nome in zip(ir.partes, ctx.plano.instrumentos)]})
    elif ctx.plano.instrumentos:
        avisos.append(f"o NotaGen escreveu {len(ir.partes)} partes para {len(ctx.plano.instrumentos)} instrumentos "
                      "pedidos; ficam os nomes dele")
    nova = _conferir(ctx.plano, atual, ir, origem="externo", operacao=ctx.operacao)
    return nova, ctx.model_copy(update={"saidas_externas": {**ctx.saidas_externas, k: xml},
                                        "avisos": [*ctx.avisos, *(neutralizar(a) for a in avisos)]})


def _com_instrumento(parte: Parte, nome: str) -> Parte:
    """A parte do NotaGen com o nome (e a tessitura, quando conhecida) do instrumento pedido no plano."""
    try:
        tessitura = instrumento(nome).tessitura
    except ValueError:
        tessitura = parte.tessitura
    return parte.model_copy(update={"instrumento": nome.strip().capitalize(), "tessitura": tessitura})


def _validar(nova: MusicIR) -> MusicIR:
    """Revalida (model_copy não revalida) e exige que seja notável (ex.: nada atravessa a barra de compasso)."""
    try:
        nova = MusicIR.model_validate(nova.model_dump())
        para_musicxml(nova)
    except ValueError as exc:
        raise Recusa(_primeira_linha(exc)) from exc
    return nova


def _carimbar(ir: MusicIR, ids: set[str], origem: Origem, operacao: str, autor: str | None = None) -> MusicIR:
    """Proveniência do motor + id da operação nos eventos criados ou alterados (o autor do OpenMusic fica)."""
    def prov(atual: Proveniencia) -> Proveniencia:
        return Proveniencia(origem=origem, operacao=operacao,
                            autor=autor or (atual.autor if atual.origem == origem else None))

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
    if atual is None:  # peça nova: seleção e restrições valem sobre o que o plano acabou de criar
        return
    partes = {p.id for p in atual.partes}
    cria_peca = any(op.procedimento in CATALOGO and not CATALOGO[op.procedimento].requer_composicao
                    for op in plano.operacoes)  # peça nova: as partes ainda não existem (a Situation confere)
    if not cria_peca and (fora := sorted({i for r in plano.restricoes for i in r.partes if i not in partes})):
        raise Recusa(f"as restrições citam partes que não existem na composição: {fora}.")
    if sel.total:
        return
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
        if opcoes := [o for c in motores.values() for o in c.opcoes]:
            linhas.append(f"    - condicao (texto exato, uma destas): {' | '.join(opcoes)}")
    return "\n".join(linhas)


def _texto(parametros: dict[str, float], proc: Procedimento) -> str:
    return ", ".join(f"{k}={v:g} {proc.parametros[k].unidade}".rstrip() for k, v in parametros.items())

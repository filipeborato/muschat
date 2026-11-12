"""Roteador: Plano + modo + Registro -> etapas (motor e procedimento) com justificativa, ou recusa com alternativas.

Regras explícitas e ordenadas (tabela em docs/adr/0002-regras-do-roteador.md); nenhuma pontuação de "qualidade".
  R1 adequação: só motores que declaram o procedimento.
  R2 disponibilidade: motor indisponível nesta máquina sai (o motivo vai para a justificativa).
  R3 escolha manual: Python ou OpenMusic escolhido pelo compositor é respeitado; se não der, recusa com alternativas,
     sem executar outro motor.
  R4 híbrido: cada operação vai para o OpenMusic quando ele a executa aqui, o resto para o Python; sem nenhuma etapa
     OpenMusic possível, recusa.
  R5 automático: menos intervenção manual primeiro (automatico < intercambio_arquivos < manual), o que também é o
     mais reprodutível.
  R6 desempate fixo: Python (formato nativo da MusicIR, sem conversão com perda, menos etapas).
Preservação de restrições não desempata: o verificador roda depois de qualquer motor.
"""

from typing import Literal

from pydantic import BaseModel

from muschat.plano import Plano
from muschat.registro import NOME_DO_MOTOR, PREFERENCIA, Motor, Registro

Modo = Literal["automatico", "python", "openmusic", "hibrido"]
MODOS: dict[Modo, str] = {"automatico": "Automático", "python": "Python/Abjad", "openmusic": "OpenMusic",
                          "hibrido": "Híbrido"}


class Etapa(BaseModel):
    motor: Motor
    procedimento: str


class Decisao(BaseModel):
    modo: Modo
    etapas: list[Etapa]
    justificativa: list[str]


class SemRota(Exception):
    """O modo pedido não executa o plano; a mensagem traz o motivo e as alternativas."""


def rotear(plano: Plano, modo: Modo, registro: Registro) -> Decisao:
    efetivo, porque = modo, []
    if modo == "automatico" and plano.motor != "automatico":
        efetivo = plano.motor
        porque.append(f"O pedido escolheu o motor {MODOS[efetivo]}; vale como escolha manual (R3).")
    elif plano.motor not in ("automatico", modo):
        porque.append(f"O pedido citou o motor {MODOS[plano.motor]}, mas o seletor está em {MODOS[modo]}: vale o "
                      "seletor (R3). Troque o seletor para usar o outro motor.")
    etapas, linhas = [], list(porque)
    for op in plano.operacoes:
        motor, linha = _escolher(op.procedimento, efetivo, registro)
        etapas.append(Etapa(motor=motor, procedimento=op.procedimento))
        linhas.append(linha)
    if efetivo == "hibrido" and not any(e.motor == "openmusic" for e in etapas):
        raise SemRota("o modo Híbrido precisa de uma etapa no OpenMusic, e nenhuma operação do plano roda no "
                      f"OpenMusic aqui. {_situacao_om(registro)} Alternativas: modo Automático ou Python/Abjad.")
    return Decisao(modo=efetivo, etapas=etapas, justificativa=linhas)


def _escolher(procedimento: str, modo: Modo, registro: Registro) -> tuple[Motor, str]:
    impl = registro.implementacoes(procedimento)  # R1 + R2: só o que existe e roda aqui
    disponiveis = {m: c for m, c in impl.items() if c.estado != "indisponivel"}
    if modo in ("python", "openmusic"):  # R3
        if modo in disponiveis:
            return modo, f"{procedimento}: {MODOS[modo]}, como escolhido ({_estado(disponiveis[modo].estado)})."
        raise SemRota(f"o motor {MODOS[modo]} não executa {procedimento} nesta máquina. "
                      f"{_por_que_nao(procedimento, modo, registro)} Alternativas: {_alternativas(disponiveis)}")
    if not disponiveis:
        raise SemRota(f"nenhum motor executa {procedimento} nesta máquina.")
    if modo == "hibrido" and "openmusic" in disponiveis:  # R4
        return "openmusic", (f"{procedimento}: OpenMusic, etapa do modo Híbrido "
                             f"({_estado(disponiveis['openmusic'].estado)}).")
    if modo == "hibrido":
        return "python", f"{procedimento}: Python/Abjad, o OpenMusic não tem esse procedimento aqui (R4)."
    automaticos = {m: c for m, c in disponiveis.items() if c.estado == "automatico"}
    if not automaticos:
        raise SemRota("Esta operacao exige modo assistido explicito; selecione OpenMusic ou Hibrido.")
    # R5 e R6: menos intervenção manual; empate fica com o Python
    escolhido = min(automaticos, key=lambda m: (PREFERENCIA[disponiveis[m].estado], m != "python"))
    outros = [m for m in disponiveis if m != escolhido]
    if outros:
        o = outros[0]
        return escolhido, (f"{procedimento}: {NOME_DO_MOTOR[escolhido]} ({_estado(disponiveis[escolhido].estado)}). "
                           f"O {NOME_DO_MOTOR[o]} também faz, mas aqui é {_estado(disponiveis[o].estado)}; "
                           "o automático prefere menos intervenção manual (R5).")
    motivo = "" if registro.capacidade("OpenMusic") is None or escolhido == "openmusic" else f" {_situacao_om(registro)}"
    return escolhido, f"{procedimento}: {NOME_DO_MOTOR[escolhido]}, o único que executa aqui.{motivo}"


def _estado(estado: str) -> str:
    return {"automatico": "automático", "intercambio_arquivos": "por intercâmbio de arquivos",
            "manual": "assistido: você roda o patch", "indisponivel": "indisponível"}[estado]


def _situacao_om(registro: Registro) -> str:
    om = registro.capacidade("OpenMusic")
    if om is None or om.estado == "indisponivel":
        return f"OpenMusic indisponível: {om.motivo if om else 'não detectado'}."
    libs = [c for c in registro.capacidades if c.tipo == "biblioteca"]
    return " ".join(f"{c.nome}: {c.motivo}." for c in libs if c.estado == "indisponivel") or "OpenMusic disponível."


def _por_que_nao(procedimento: str, motor: Motor, registro: Registro) -> str:
    if motor == "openmusic":
        om = registro.capacidade("OpenMusic")
        if om is None:
            return "Integração experimental desativada; habilite MUSCHAT_OPENMUSIC=1 para usar OpenMusic."
        if om.estado == "indisponivel":
            return _situacao_om(registro)
        if not any(procedimento in c.procedimentos for c in registro.capacidades if c.tipo == "biblioteca"):
            return f"Não há patch aprovado de {procedimento} para o OpenMusic."
        return _situacao_om(registro)
    return f"{procedimento} não está no catálogo Python."


def _alternativas(disponiveis: dict) -> str:
    if not disponiveis:
        return "nenhum outro motor executa isso aqui."
    return "; ".join(f"{MODOS[m]} ({_estado(c.estado)})" for m, c in disponiveis.items()) + "."

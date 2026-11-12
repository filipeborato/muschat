"""Composição da sessão: Versões imutáveis da MusicIR, com desfazer, voltar, comparar, e a etapa assistida que espera
o compositor (Pendência). Serializa em JSON (Versões + Pendência)."""

import json
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from muschat.musicir import Diferenca, MusicIR, comparar
from muschat.openmusic import Tarefa
from muschat.plano import Plano
from muschat.roteador import Decisao


@dataclass(frozen=True)
class Versao:
    ir: MusicIR
    resumo: str  # o que a operação fez, em uma linha
    experimento: dict  # pedido, modelo, plano, decisão, etapas, parâmetros, semente, intermediários...
    anterior: int | None  # índice da Versão de onde esta partiu (desfazer volta para ela)
    data: datetime = field(default_factory=datetime.now)


class Pendencia(BaseModel):
    """Lista de etapas parada numa etapa assistida do OpenMusic: tudo o que é preciso para retomar dali."""

    pedido: str
    modelo: str
    plano: Plano
    decisao: Decisao
    parametros: list[dict[str, float]]
    semente: int
    operacao: str
    base: int | None  # Versão de onde a operação partiu
    etapa: int  # índice da etapa que espera o compositor
    atual: MusicIR | None  # MusicIR antes da etapa que espera
    intermediarios: list[MusicIR]
    tarefa: Tarefa | None = None  # preenchida quando a lista pausa numa etapa do OpenMusic


class Composicao:
    """Histórico só cresce: desfazer e voltar mudam a Versão atual, nenhuma Versão é apagada."""

    def __init__(self) -> None:
        self.versoes: list[Versao] = []
        self.indice: int | None = None
        self.pendente: Pendencia | None = None

    @property
    def atual(self) -> Versao | None:
        return None if self.indice is None else self.versoes[self.indice]

    def adicionar(self, ir: MusicIR, resumo: str, experimento: dict, *, anterior: int | None) -> Versao:
        versao = Versao(ir, resumo, experimento, anterior)
        self.versoes.append(versao)
        self.indice = len(self.versoes) - 1
        return versao

    def desfazer(self) -> None:
        if self.atual is not None and self.atual.anterior is not None:
            self.indice = self.atual.anterior

    def voltar(self, indice: int) -> None:
        if not 0 <= indice < len(self.versoes):
            raise IndexError(f"versão {indice} não existe")
        self.indice = indice

    def comparar(self, de: int, para: int) -> Diferenca:
        return comparar(self.versoes[de].ir, self.versoes[para].ir)

    def para_json(self) -> str:
        return json.dumps({
            "indice": self.indice,
            "versoes": [{"ir": v.ir.model_dump(mode="json"), "resumo": v.resumo, "experimento": v.experimento,
                         "anterior": v.anterior, "data": v.data.isoformat()} for v in self.versoes],
            "pendente": self.pendente.model_dump(mode="json") if self.pendente else None,
        }, ensure_ascii=False)

    @classmethod
    def de_json(cls, texto: str) -> "Composicao":
        """Reconstrói e revalida tudo (o JSON pode vir de fora: nada entra sem passar pelos schemas)."""
        try:
            dados = json.loads(texto)
            c = cls()
            c.versoes = [Versao(MusicIR.model_validate(v["ir"]), str(v["resumo"]), dict(v["experimento"]),
                                v["anterior"], datetime.fromisoformat(v["data"])) for v in dados["versoes"]]
            c.indice = dados["indice"]
            c.pendente = Pendencia.model_validate(dados["pendente"]) if dados.get("pendente") else None
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"JSON de composição incompleto: {exc}") from exc
        n = len(c.versoes)

        def valido(i: object) -> bool:
            return i is None or (isinstance(i, int) and 0 <= i < n)

        if not valido(c.indice) or (n and c.indice is None) or not all(valido(v.anterior) for v in c.versoes):
            raise ValueError("JSON de composição com índice de versão inválido")
        p = c.pendente
        if p and (not valido(p.base) or not 0 <= p.etapa < len(p.decisao.etapas)
                  or len(p.parametros) != len(p.decisao.etapas) or p.tarefa is None
                  or p.decisao.etapas[p.etapa].motor != "openmusic"):
            raise ValueError("JSON de composição com etapa pendente incoerente")
        return c

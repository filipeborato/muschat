"""Composição da sessão: Versões imutáveis da MusicIR, com desfazer, voltar e comparar."""

from dataclasses import dataclass, field
from datetime import datetime

from muschat.musicir import Diferenca, MusicIR, comparar


@dataclass(frozen=True)
class Versao:
    ir: MusicIR
    resumo: str  # o que a operação fez, em uma linha
    experimento: dict  # pedido, modelo, plano, procedimentos, parâmetros, semente, operação
    anterior: int | None  # índice da Versão de onde esta partiu (desfazer volta para ela)
    data: datetime = field(default_factory=datetime.now)


class Composicao:
    """Histórico só cresce: desfazer e voltar mudam a Versão atual, nenhuma Versão é apagada."""

    def __init__(self) -> None:
        self.versoes: list[Versao] = []
        self.indice: int | None = None

    @property
    def atual(self) -> Versao | None:
        return None if self.indice is None else self.versoes[self.indice]

    def adicionar(self, ir: MusicIR, resumo: str, experimento: dict) -> Versao:
        versao = Versao(ir, resumo, experimento, self.indice)
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

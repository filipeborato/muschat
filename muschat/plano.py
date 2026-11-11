"""Plano composicional: o que o modelo devolve (response_schema) antes de qualquer procedimento rodar."""

from typing import Literal

from pydantic import BaseModel, Field

from muschat.restricoes import Restricao


class Parametro(BaseModel):
    nome: str
    valor: float


class Operacao(BaseModel):
    procedimento: str = Field(description="Nome de um procedimento do catálogo.")
    parametros: list[Parametro] = Field(default_factory=list, description="Ausente = valor padrão do catálogo.")


class Selecao(BaseModel):
    """Onde a operação pode mexer; vazio = a peça inteira. Ids da composição atual."""

    partes: list[str] = Field(default_factory=list)
    vozes: list[str] = Field(default_factory=list)
    compasso_inicial: int | None = None
    compasso_final: int | None = None

    def contem(self, parte: str, voz: str | None, compasso: int | None) -> bool:
        """Partes/vozes entram com compasso None: só estão na seleção se ela não recorta compassos."""
        sem_recorte = self.compasso_inicial is None and self.compasso_final is None
        if compasso is None:
            no_trecho = sem_recorte
        else:
            no_trecho = (self.compasso_inicial or 1) <= compasso <= (self.compasso_final or compasso)
        return ((not self.partes or parte in self.partes) and (not self.vozes or voz in self.vozes) and no_trecho)

    @property
    def total(self) -> bool:
        return self == Selecao()


class Plano(BaseModel):
    objetivo: str
    instrumentos: list[str] = Field(default_factory=list, max_length=16,
                                    description='Um por parte: ["violino", "violino", ...].')
    estrutura: str = Field(default="", description="Estrutura temporal (compassos, seções).")
    tecnicas: list[str] = Field(default_factory=list)
    restricoes: list[Restricao] = Field(default_factory=list, description="Verificadas depois da operação.")
    hipoteses: list[str] = Field(default_factory=list, description='Pedido subjetivo traduzido em parâmetros: '
                                 '"fragmentado = densidade 1 -> 3 ataques/semínima nos compassos 7-8".')
    operacoes: list[Operacao] = Field(min_length=1, max_length=8)
    selecao: Selecao = Field(default_factory=Selecao)
    motor: Literal["automatico", "python", "openmusic"] = "automatico"
    criterios: list[str] = Field(default_factory=list, description="Critérios de validação do resultado.")
    semente: int | None = Field(default=None, description="Só quando o usuário pede reprodução de uma semente.")

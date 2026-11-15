"""Plano composicional: o que o modelo devolve (response_schema) antes de qualquer procedimento rodar."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from muschat.restricoes import Restricao


class Parametro(BaseModel):
    nome: str
    valor: float


class Operacao(BaseModel):
    secao: str = Field(default="", description="Nome da seção alvo; vazio usa a seleção do plano.")
    procedimento: str = Field(description="Nome de um procedimento do catálogo.")
    parametros: list[Parametro] = Field(default_factory=list, description="Ausente = valor padrão do catálogo.")
    condicao: str = Field(default="", description="Só para gerar_notagen: uma das condições listadas no catálogo.")


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


class Secao(BaseModel):
    nome: str = Field(min_length=1)
    carater: str
    compasso_inicial: int = Field(ge=1)
    compasso_final: int = Field(ge=1)
    material: str
    tecnicas: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar(self) -> "Secao":
        if self.compasso_final < self.compasso_inicial:
            raise ValueError("seção com compassos invertidos")
        return self


class Plano(BaseModel):
    secoes: list[Secao] = Field(default_factory=list)
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
    motor: Literal["automatico", "python", "openmusic", "hibrido", "notagen"] = Field(
        default="automatico", description="Só se o usuário pedir um motor; senão automatico.")
    criterios: list[str] = Field(default_factory=list, description="Critérios de validação do resultado.")
    semente: int | None = Field(default=None, description="Só quando o usuário pede reprodução de uma semente.")

    @model_validator(mode="after")
    def validar_secoes(self) -> "Plano":
        nomes = [s.nome.casefold() for s in self.secoes]
        if len(set(nomes)) != len(nomes):
            raise ValueError("nomes de seções repetidos")
        ordenadas = sorted(self.secoes, key=lambda s: s.compasso_inicial)
        if any(a.compasso_final >= b.compasso_inicial for a, b in zip(ordenadas, ordenadas[1:])):
            raise ValueError("seções sobrepostas")
        return self

    def para_etapa(self, k: int) -> "Plano":
        nome = self.operacoes[k].secao
        if not nome:
            return self
        secao = next((s for s in self.secoes if s.nome.casefold() == nome.casefold()), None)
        if secao is None:
            raise ValueError(f"seção desconhecida: {nome}")
        return self.model_copy(update={"selecao": self.selecao.model_copy(update={
            "compasso_inicial": secao.compasso_inicial, "compasso_final": secao.compasso_final}),
            "tecnicas": secao.tecnicas or self.tecnicas})

"""Fonte única de receitas para planejamento e documentação: python -m muschat.receitas."""

from dataclasses import dataclass

from muschat.plano import Operacao, Parametro, Plano


@dataclass(frozen=True)
class Receita:
    descricao: str
    cadeia: tuple[tuple[str, dict[str, float]], ...]


RECEITAS = {
    "scelsi": Receita("Desvio microtonal e glissandos dentro dos gestos.", (
        ("desafinar", {"desvio": 100, "resolucao": 2, "curva": 1, "glissandos": 0.7}),)),
    "lachenmann": Receita("Alturas cedem progressivamente a ruídos instrumentais tocáveis.", (
        ("substituir_timbre", {"inicial": 0.2, "final": 0.9, "familia_ruido": 0}),)),
    "sciarrino": Receita("Silêncios, prolongamentos, harmônicos/whistle tones e pppp.", (
        ("rarefazer", {"manter": 0.3, "harmonicos": 0.7, "dinamica": 0, "acentos": 0.03}),)),
    "ligeti": Receita("Cânone próximo com espalhamento de alturas e movimento microtonal.", (
        ("micropolifonia", {"parte_origem": 1, "defasagem": 1, "variacao": 0, "espalhamento": 150}),
        ("desafinar", {"desvio": 50, "resolucao": 2, "curva": 0, "glissandos": 0}))),
    "espectral": Receita("Parciais distribuídos por registro, seguidos de desvio microtonal.", (
        ("espectralizar", {"fundamental": 3600, "parciais": 16, "microtom": 1, "salto_maximo": 700}),
        ("desafinar", {"desvio": 50, "resolucao": 2, "curva": 0, "glissandos": 0.4}))),
    "ritmo_elastico": Receita("Gestos em quiálteras irregulares e respirações.", (
        ("deformar_ritmo", {"proporcao": 0.6, "quialteras": 15, "minimo": 0.5, "maximo": 1}),)),
}


def documentacao() -> str:
    linhas = ["# Receitas estéticas", "", "Pontos de partida por referência estética, não imitações de compositores.",
              "A fonte é muschat/receitas.py; regenere com `uv run python -m muschat.receitas`.", "",
              "Para material novo, preceda a cadeia por geração pelo NotaGen (condição instalada); material existente pode ser transformado.", ""]
    for nome, receita in RECEITAS.items():
        cadeia = " → ".join(f"{op}({', '.join(f'{k}={v:g}' for k, v in params.items())})"
                            for op, params in receita.cadeia)
        linhas.append(f"- **{nome}**: {receita.descricao} {cadeia}")
    linhas += ["", "## Limites de escrita", "",
        "A micropolifonia quantiza ataques e finais a 1/64 de semibreve e limita a cauda à seleção. "
        "A deformação aproxima fatores às figuras disponíveis, usa pausas seguintes para alongar e recusa "
        "gestos já em quiálteras ou curtos demais, sem alterar a composição. Harmônicos de cordas usam "
        "afinação padrão e parciais 2–8. A avaliação estética depende da escuta do compositor."]
    return "\n".join(linhas) + "\n"


def expandir(plano: Plano) -> Plano:
    if not plano.receita:
        return plano
    if plano.receita not in RECEITAS:
        raise ValueError(f"receita desconhecida: {plano.receita}")
    receita = RECEITAS[plano.receita]
    nomes = [nome for nome, _ in receita.cadeia]
    existentes = [op for op in plano.operacoes if op.procedimento in nomes]
    if [o.procedimento for o in existentes] == nomes:
        operacoes = plano.operacoes
    else:
        if existentes:
            raise ValueError("cadeia parcial da receita: forneça a cadeia completa ou apenas a operação de geração")
        cadeia = [Operacao(procedimento=nome, parametros=[Parametro(nome=k, valor=v) for k, v in params.items()])
                  for nome, params in receita.cadeia]
        operacoes = [*plano.operacoes, *cadeia]
    hipotese = f"Receita {plano.receita}: {plano.motivo_receita or receita.descricao}"
    return Plano.model_validate({**plano.model_dump(), "operacoes": [o.model_dump() for o in operacoes],
        "hipoteses": plano.hipoteses if hipotese in plano.hipoteses else [*plano.hipoteses, hipotese]})


if __name__ == "__main__":
    from pathlib import Path
    Path("docs/receitas.md").write_text(documentacao(), encoding="utf-8")

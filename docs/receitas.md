# Receitas estéticas

Pontos de partida por referência estética, não imitações de compositores.
A fonte é muschat/receitas.py; regenere com `uv run python -m muschat.receitas`.

Para material novo, preceda a cadeia por geração pelo NotaGen (condição instalada); material existente pode ser transformado.

- **scelsi**: Desvio microtonal e glissandos dentro dos gestos. desafinar(desvio=100, resolucao=2, curva=1, glissandos=0.7)
- **lachenmann**: Alturas cedem progressivamente a ruídos instrumentais tocáveis. substituir_timbre(inicial=0.2, final=0.9, familia_ruido=0)
- **sciarrino**: Silêncios, prolongamentos, harmônicos/whistle tones e pppp. rarefazer(manter=0.3, harmonicos=0.7, dinamica=0, acentos=0.03)
- **ligeti**: Cânone próximo com espalhamento de alturas e movimento microtonal. micropolifonia(parte_origem=1, defasagem=1, variacao=0, espalhamento=150) → desafinar(desvio=50, resolucao=2, curva=0, glissandos=0)
- **espectral**: Parciais distribuídos por registro, seguidos de desvio microtonal. espectralizar(fundamental=3600, parciais=16, microtom=1, salto_maximo=700) → desafinar(desvio=50, resolucao=2, curva=0, glissandos=0.4)
- **ritmo_elastico**: Gestos em quiálteras irregulares e respirações. deformar_ritmo(proporcao=0.6, quialteras=15, minimo=0.5, maximo=1)

## Limites de escrita

A micropolifonia quantiza ataques e finais a 1/64 de semibreve e limita a cauda à seleção. A deformação aproxima fatores às figuras disponíveis, usa pausas seguintes para alongar e recusa gestos já em quiálteras ou curtos demais, sem alterar a composição. Harmônicos de cordas usam afinação padrão e parciais 2–8. A avaliação estética depende da escuta do compositor.

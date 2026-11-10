class FakeEmbedder:
    """Vetor determinístico: [contagem de 'a', contagem de 'b', 1]."""

    def __init__(self):
        self.chamadas = []

    def embutir(self, textos, tipo):
        self.chamadas.append((list(textos), tipo))
        return [[t.count("a"), t.count("b"), 1.0] for t in textos]

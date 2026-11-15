class FakeEmbedder:
    """Deterministic vector: [count of 'a', count of 'b', 1]."""

    def __init__(self):
        self.calls = []

    def embed(self, texts, kind):
        self.calls.append((list(texts), kind))
        return [[t.count("a"), t.count("b"), 1.0] for t in texts]

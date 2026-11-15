"""Bridge NotaGen (opcional): modelo local de geração simbólica (MIT, github.com/ElectricAlexis/NotaGen) que escreve
frases e ritmo de música de câmara a partir de uma condição período-compositor-instrumentação.

Roda num ambiente Python próprio (torch/CUDA, versões incompatíveis com o Muschat), por processo: o Muschat chama
`servicos/notagen_gerar.py` com o python desse ambiente e lê o MusicXML que ele grava. Nada do modelo é importado aqui.
"""

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

PESOS = "weights_notagenx_p_size_16_p_length_1024_p_layers_20_h_size_1280.pth"  # NotaGen-X, em gradio/
SCRIPT = Path(__file__).resolve().parent.parent / "servicos" / "notagen_gerar.py"
TIMEOUT = 900  # s; 16 compassos levam ~1 min numa RTX 4060, o resto é carregar 6 GB de pesos


class Compositor(Protocol):
    """Gera material novo e devolve MusicXML. Levanta ValueError com a mensagem ao usuário quando falha."""

    def gerar(self, condicao: str, compassos: int, semente: int) -> bytes: ...


@dataclass(frozen=True)
class InstalacaoNotaGen:
    python: str
    pasta: str  # raiz do repositório NotaGen
    condicoes: tuple[str, ...]  # "Período_Compositor_Instrumentação", as que o NotaGen-X aceita


def detectar_notagen(python: str, pasta: str) -> InstalacaoNotaGen | str:
    """Instalação utilizável, ou o motivo de não ser (vai para o Registro)."""
    if not Path(python).is_file():
        return f"python do ambiente do NotaGen não encontrado em {python} (defina NOTAGEN_PYTHON)"
    gradio = Path(pasta) / "gradio"
    if not (gradio / PESOS).is_file():
        return f"pesos do NotaGen-X não encontrados em {gradio} (baixe {PESOS}; ver README)"
    try:
        texto = (gradio / "prompts.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"lista de condições {gradio / 'prompts.txt'} ilegível ({exc.__class__.__name__}); reinstale o NotaGen"
    condicoes = tuple(linha.strip() for linha in texto.splitlines() if linha.strip())
    # absolutos: o processo roda com o diretório de trabalho em gradio/
    return InstalacaoNotaGen(str(Path(python).resolve()), str(Path(pasta).resolve()), condicoes)


class NotaGenLocal:
    """Compositor que roda o NotaGen-X na GPU local, um processo por geração."""

    def __init__(self, instalacao: InstalacaoNotaGen):
        self.instalacao = instalacao

    def gerar(self, condicao: str, compassos: int, semente: int) -> bytes:
        if condicao not in self.instalacao.condicoes:
            raise ValueError(f"condição {condicao!r} não é aceita pelo NotaGen-X")
        # shortcut: carrega o modelo (6 GB) a cada geração; servidor local que mantém o modelo na GPU se a espera
        # incomodar
        with tempfile.TemporaryDirectory(prefix="muschat-notagen-") as pasta:
            saida = Path(pasta) / "saida.musicxml"
            try:
                r = subprocess.run([self.instalacao.python, str(SCRIPT), str(saida)], cwd=Path(self.instalacao.pasta) / "gradio",
                                   input=json.dumps({"condicao": condicao, "compassos": compassos, "semente": semente}),
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"o NotaGen passou de {TIMEOUT} s sem terminar") from exc
            except OSError as exc:
                raise ValueError(f"não foi possível rodar o NotaGen ({exc})") from exc
            if r.returncode != 0 or not saida.is_file():
                erro = (r.stderr.strip().splitlines() or ["sem mensagem"])[-1]
                if "out of memory" in r.stderr.lower():
                    erro = "a GPU ficou sem memória (feche outros programas que usam a placa)"
                raise ValueError(f"o NotaGen falhou: {erro[:300]}")
            return saida.read_bytes()

"""Gera N compassos com o NotaGen-X e grava MusicXML. Roda no ambiente Python do NotaGen (não no do Muschat), com o
diretório de trabalho em NotaGen/gradio; o Muschat o chama por processo (muschat/notagen.py).

    echo '{"condicao": "Romantic_Ravel, Maurice_Chamber", "compassos": 8, "semente": 1}' | python notagen_gerar.py saida.musicxml

O inference_patch original gera o movimento inteiro (100+ compassos, sem cache de atenção, ~20 s por compasso numa
RTX 4060) e recomeça do zero passados 10 min. Aqui o laço de amostragem é o mesmo, mas para em N compassos.
"""

import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

_carregar = torch.load


def _na_cpu(*args, **kwargs):
    """O checkpoint (6 GB, com estado de treino) vai para a CPU; só o modelo sobe para a GPU (cabe em 8 GB)."""
    kwargs["map_location"] = "cpu"
    return _carregar(*args, **kwargs)


torch.load = _na_cpu
sys.path.insert(0, str(Path.cwd()))
import inference as ng  # noqa: E402  (carrega o modelo na importação)


LIMITE_TEXTO = 102_400  # caracteres de ABC, o mesmo teto do inference_patch original


def gerar(condicao: str, compassos: int) -> str:
    periodo, *meio, formacao = condicao.split("_")
    compositor = "_".join(meio)
    p = ng.patchilizer
    linhas = [f"%{periodo}\n", f"%{compositor}\n", f"%{formacao}\n"]
    patches = [[ord(c) for c in x] + [p.special_token_id] * (ng.PATCH_SIZE - len(x))
               for x in p.patchilize_metadata(linhas)]
    patches.insert(0, [p.bos_token_id] * (ng.PATCH_SIZE - 1) + [p.eos_token_id])
    entrada = torch.tensor(patches, device=ng.device).reshape(1, -1)
    texto, corpo = "".join(linhas), False
    with torch.inference_mode():
        while texto.count("\n[r:") < compassos + 1:  # +1: a linha seguinte começou, a N-ésima está completa
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                patch = ng.model.generate(entrada.unsqueeze(0), top_k=ng.TOP_K, top_p=ng.TOP_P,
                                          temperature=ng.TEMPERATURE)
                if not corpo and p.decode([patch]).startswith("[r:"):  # como no original: o corpo começa em [r:0/
                    corpo = True
                    r0 = torch.tensor([ord(c) for c in "[r:0/"], device=ng.device).unsqueeze(0)
                    patch = [ord(c) for c in "[r:0/"] + ng.model.generate(
                        torch.concat([entrada, r0], axis=-1).unsqueeze(0), top_k=ng.TOP_K, top_p=ng.TOP_P,
                        temperature=ng.TEMPERATURE)
            if patch[0] == p.bos_token_id and patch[1] == p.eos_token_id:
                break  # a peça acabou antes de N compassos
            texto += p.decode([patch])
            if len(texto) > LIMITE_TEXTO:
                sys.exit(f"o NotaGen passou de {LIMITE_TEXTO} caracteres sem fechar {compassos} compassos")
            fim = False
            for j in range(len(patch)):
                if fim:
                    patch[j] = p.special_token_id
                fim = fim or patch[j] == p.eos_token_id
            entrada = torch.cat([entrada, torch.tensor([patch], device=ng.device)], dim=1)
    linhas = [x + "\n" for x in texto.split("\n") if x]
    corpo_ini = next((i for i, x in enumerate(linhas) if x.startswith("[r:")), None)
    if corpo_ini is None:
        sys.exit("o NotaGen terminou antes de escrever qualquer compasso; tente outra semente")
    linhas = linhas[:corpo_ini + compassos]  # descarta a linha incompleta
    linhas = [x for x in ng.rest_unreduce(linhas) if not (x.startswith("%") and not x.startswith("%%"))]
    return "X:1\n" + "".join(linhas)


def main() -> None:
    if not torch.cuda.is_available():
        sys.exit("sem GPU CUDA visível para o torch do ambiente do NotaGen")
    pedido = json.load(sys.stdin)
    semente = int(pedido["semente"])
    random.seed(semente)
    np.random.seed(semente % 2**32)
    torch.manual_seed(semente)
    abc = gerar(pedido["condicao"], int(pedido["compassos"]))
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "peca.abc"
        arquivo.write_text(abc, encoding="utf-8")
        subprocess.run([sys.executable, "abc2xml.py", "-o", pasta, str(arquivo)], check=True,
                       stdout=subprocess.DEVNULL)
        shutil.copyfile(Path(pasta) / "peca.xml", sys.argv[1])


if __name__ == "__main__":
    main()

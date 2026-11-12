"""Tickets 12–15: Chaos, Situation, OMChroma/Csound e OM-SuperVP pela ponte assistida (executor fake: a saída do
OpenMusic é escrita à mão; o teste real roda só onde a biblioteca está instalada)."""

from dataclasses import replace
from pathlib import Path

from muschat.musicir import Nota
from muschat.openmusic import PATCHES, InstalacaoOM
from muschat.orquestrador import retomar
from muschat.registro import detectar
from tests.test_composicao import _plano, _quarteto
from tests.test_motores import _rodar

LIBS = {n: Path(f"C:/OM/Libraries/{n}") for n in ("esquisse", "chaos", "situation", "om2csound", "omchroma")}
OM = InstalacaoOM(Path("C:/OM/OM 8.0.exe"), "8.0", LIBS)
APROVADOS = {nome: replace(p, arquivo=p.arquivo or __file__, validado=True) for nome, p in PATCHES.items()}
COM_LIBS = detectar(lilypond=None, openmusic=OM, abjad_versao="3.31", which=lambda e: f"C:/bin/{e}.exe",
                    patches=APROVADOS)


def _verhulst(x0: float, lam: float, n: int) -> list[float]:
    """A fórmula da documentação da Chaos (orbitals.lisp): x(n) = x(n-1) + lambda * x(n-1) * (1 - x(n-1))."""
    xs = [x0]
    for _ in range(n):
        xs.append(xs[-1] + lam * xs[-1] * (1 - xs[-1]))
    return xs


def _ataques(ir, parte: int, compasso: int) -> int:
    inicio = ir.inicios()[compasso - 1]
    fim = inicio + ir.compassos[compasso - 1].comprimento
    return sum(1 for v in ir.partes[parte].vozes for e in v.eventos
               if isinstance(e, Nota) and inicio <= e.inicio < fim)


# --- 12: Chaos -----------------------------------------------------------------------------------------------------

def test_chaos_verhulst_controla_a_densidade_de_cada_compasso(tmp_path):
    c = _quarteto(4)
    plano = _plano("densidade_caotica", {"x0": 0.1, "turbulencia": 2.8, "minimo": 0, "maximo": 2})
    c, r, _ = _rodar(plano, modo="openmusic", registro=COM_LIBS, composicao=c, pasta=tmp_path)
    assert r.tarefa is not None, r.resumo
    assert (Path(r.tarefa.pasta) / "entrada.txt").read_text() == "0.1 2.8 3\n"  # 4 compassos = 3 iterações
    valores = _verhulst(0.1, 2.8, 3)  # 0.1, 0.352, 0.9907, 1.0166: mínimo no c.1, máximo no c.4
    r2 = retomar(c, " ".join(f"{v:.6f}" for v in valores))
    v = r2.versao
    assert v is not None, r2.resumo
    for parte in range(4):
        assert _ataques(v.ir, parte, 1) == 0  # mínimo da série -> 0 ataques por semínima
        assert _ataques(v.ir, parte, 4) == 8  # máximo -> 2 ataques por semínima em 4/4
    assert v.experimento["etapas"][0]["motor"] == "openmusic"


def test_chaos_saida_com_quantidade_errada_e_recusada(tmp_path):
    c = _quarteto(4)
    plano = _plano("densidade_caotica", {"x0": 0.1, "turbulencia": 2.8})
    c, r, _ = _rodar(plano, modo="openmusic", registro=COM_LIBS, composicao=c, pasta=tmp_path)
    r2 = retomar(c, "0.1 0.3")
    assert r2.versao is None and "4 valores" in r2.resumo and c.pendente is not None


# --- 13: Situation ---------------------------------------------------------------------------------------------------

def _situation(tmp_path, base=5500, passo=100):
    plano = _plano("distribuir_restricoes", {"alturas": 12, "base": base, "passo": passo})
    return _rodar(plano, modo="openmusic", registro=COM_LIBS, pasta=tmp_path)


def test_situation_entrada_tem_um_dominio_por_vaga_e_a_solucao_vira_quarteto(tmp_path):
    c, r, _ = _situation(tmp_path)
    linhas = (Path(r.tarefa.pasta) / "entrada.txt").read_text().splitlines()
    assert linhas[0] == "12" and len(linhas) == 13
    assert linhas[4].split()[:2] == ["12", "5500"]  # vaga 3 = violoncelo: alcança as 12 alturas de 5500 a 6600
    solucao = [5500 + 100 * k for k in range(12)]  # vagas na ordem: cada altura uma vez, todas na tessitura
    v = retomar(c, " ".join(map(str, solucao))).versao
    assert v is not None and [p.instrumento for p in v.ir.partes] == ["Violino", "Violino", "Viola", "Violoncelo"]
    assert len(v.ir.compassos) == 3
    assert [e.alturas[0].midicents for e in v.ir.partes[3].vozes[0].eventos] == [5800, 6200, 6600]


def test_situation_solucao_que_viola_restricao_e_rejeitada_com_o_local(tmp_path):
    c, r, _ = _situation(tmp_path, base=4800)
    ruim = [4800 + 100 * k for k in range(12)]  # vaga 0 (violino, c.1) com 4800: abaixo do Sol 3
    r2 = retomar(c, " ".join(map(str, ruim)))
    assert r2.versao is None and "tessitura" in r2.resumo and "P1 c.1" in r2.resumo and c.pendente is not None
    repetida = [5500 + 100 * k for k in range(12)]
    repetida[1] = repetida[0]
    c, r, _ = _situation(tmp_path)
    r3 = retomar(c, " ".join(map(str, repetida)))
    assert r3.versao is None and "c.1" in r3.resumo and ("uníssono" in r3.resumo or "repetida" in r3.resumo)


def test_situation_sem_solucao_lista_as_condicoes(tmp_path):
    c, r, _ = _situation(tmp_path, base=4800)  # 4800–5900: violinos alcançam só 5500–5900 (5) para 6 vagas
    r2 = retomar(c, "-1")
    assert r2.versao is None and "não achou solução" in r2.resumo
    assert "sem uníssono" in r2.resumo and "Violino: 3 vagas" in r2.resumo
    assert "Violino + Violino alcançam 5 alturas para 6 vagas" in r2.resumo


def test_patch_que_falhou_no_openmusic_mostra_o_erro_txt(tmp_path):
    c, r, _ = _situation(tmp_path)
    (Path(r.tarefa.pasta) / "erro.txt").write_text("Undefined function CSOLVER", encoding="utf-8")
    r2 = retomar(c, "-2")
    assert r2.versao is None and "Undefined function CSOLVER" in r2.resumo and c.pendente is not None


# --- 14: OMChroma + Csound -------------------------------------------------------------------------------------------

def test_sintese_envia_parciais_e_acrescenta_parte_eletronica_sem_notas(tmp_path):
    c = _quarteto(2)
    notas_antes = [e for _, _, e in c.atual.ir.eventos() if isinstance(e, Nota)]
    plano = _plano("sintese_aditiva", {"parciais_inicial": 1, "parciais_final": 3, "amplitude": -18})
    c, r, _ = _rodar(plano, modo="openmusic", registro=COM_LIBS, composicao=c, pasta=tmp_path)
    assert r.tarefa is not None, r.resumo
    entrada = (Path(r.tarefa.pasta) / "entrada.txt").read_text().splitlines()
    n = int(entrada[0])
    assert n == len(entrada) - 1 and len(notas_antes) <= n <= 3 * len(notas_antes)
    t, d, f, a = map(float, entrada[1].split())
    assert t == 0 and d > 0 and f > 20 and a == -18  # primeira nota: fundamental a -18 dB
    v = retomar(c, str(n)).versao
    assert v is not None
    eletronica = v.ir.partes[-1]
    assert eletronica.instrumento.startswith("Eletrônica") and "muschat.orc" in eletronica.instrumento  # sem áudio
    eventos = [e for voz in eletronica.vozes for e in voz.eventos]
    assert {e.tipo for e in eventos} == {"eletronico", "curva"}  # nunca nota falsa
    assert v.ir.partes[:4] == c.versoes[0].ir.partes  # as partes instrumentais não mudam


def test_csound_ausente_ou_sem_arquivos_nao_gera_audio_e_diz_por_que(tmp_path):
    from muschat.patches_om import renderizar
    assert "não deixou muschat.orc" in renderizar(tmp_path, "csound")
    (tmp_path / "muschat.orc").write_text("")
    (tmp_path / "muschat.sco").write_text("")
    assert "Csound não encontrado" in renderizar(tmp_path, None)


_ORC = "sr = 44100\nksmps = 32\nnchnls = 1\n0dbfs = 1\ninstr 1\na1 poscil 0.1, p4\nout a1\nendin\n"


def test_csound_real_renderiza_orc_e_sco(tmp_path):
    import pytest
    from muschat.patches_om import csound, renderizar
    if csound() is None:
        pytest.skip("Csound não instalado")
    (tmp_path / "muschat.orc").write_text(_ORC)
    (tmp_path / "muschat.sco").write_text("i1 0 0.2 440\n")
    assert renderizar(tmp_path, csound()).startswith("áudio em"), renderizar(tmp_path, csound())
    assert (tmp_path / "muschat.wav").stat().st_size > 1000


# --- 15: OM-SuperVP --------------------------------------------------------------------------------------------------

COM_SUPERVP = detectar(lilypond=None, openmusic=InstalacaoOM(OM.executavel, "8.0", {**LIBS, "om-supervp": Path("x")}),
                       abjad_versao="3.31", which=lambda e: f"C:/bin/{e}.exe", patches=APROVADOS)


def test_supervp_ausente_fica_indisponivel_com_o_motivo():
    sem = detectar(lilypond=None, openmusic=InstalacaoOM(OM.executavel, "8.0", {**LIBS, "om-supervp": Path("x")}),
                   abjad_versao="3.31", which=lambda e: None, patches=APROVADOS)
    assert sem.capacidade("SuperVP").estado == "indisponivel" and "supervp" in sem.capacidade("SuperVP").motivo
    assert sem.capacidade("OM-SuperVP").motivo == "instalada, mas falta o executável: supervp"
    assert "analise_supervp" not in sem.disponiveis()


def test_dado_espectral_importado_vira_evento_espectral_e_depois_notas(tmp_path):
    plano = _plano("analise_supervp", {"parciais": 2, "quadros": 2})
    plano["operacoes"].append({"procedimento": "eventos_do_espectro",
                               "parametros": [{"nome": "alturas_por_quadro", "valor": 2}]})
    c, r, _ = _rodar(plano, modo="hibrido", registro=COM_SUPERVP, pasta=tmp_path)
    assert r.tarefa is not None, r.resumo
    analise = "3  0 220 1  0 440 0.5  0.5 330 0.8"  # dois quadros: t=0 (220, 440 Hz) e t=0.5 s (330 Hz)
    v = retomar(c, analise).versao
    assert v is not None, retomar
    espectral = v.experimento["intermediarios"][0]["partes"][0]
    assert {e["tipo"] for e in espectral["vozes"][0]["eventos"]} == {"espectral"}  # o importado não tem notas
    assert v.ir.partes[0].instrumento.startswith("Análise espectral")
    assert all(e.tipo == "espectral" for e in v.ir.partes[0].vozes[0].eventos)
    notas = [e for p in v.ir.partes[1:] for voz in p.vozes for e in voz.eventos if isinstance(e, Nota)]
    assert notas and {e.proveniencia.origem for e in notas} == {"algoritmo"}

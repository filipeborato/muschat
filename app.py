"""Muschat: Streamlit chat UI."""

import io
import hashlib
import os
import re
import zipfile
from collections import Counter
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from streamlit.errors import StreamlitSecretNotFoundError

from muschat.composition import Composition
from muschat.config import (
    DEFAULT_NOTAGEN_DIR,
    DEFAULT_NOTAGEN_PYTHON,
    MissingApiKeyError,
    load_config,
)
from muschat.errors import error_message
from muschat.export import FORMAT_LOSSES, experiment_json, metrics, to_midi, version_package
from muschat.notagen import LocalNotaGen, PersistentNotaGen, NotaGenInstallation, detect_notagen
from muschat.notation.lilypond import EngravingResult, LilyPondInstallation, detect_lilypond, engrave, to_lilypond
from muschat.notation.musicxml import to_musicxml
from muschat.notation.render import render_svg
from muschat.openmusic import detect_openmusic
from muschat.orchestrator import discard_task, open_musicxml, reapply, resume
from muschat.procedures import CATALOG
from muschat.rag.index import GeminiEmbedder
from muschat.rag.search import open_retriever
from muschat.reasoning import respond, version_score_view
from muschat.registry import (
    ENGINE_NAME,
    STATE_LABEL,
    Registry,
    detect_capabilities as detect_registry,
)
from muschat.router import MODES
from muschat.score_ir import ScoreIR

load_dotenv()  # optional .env; does not overwrite already-defined environment variables

st.set_page_config(page_title="Muschat", page_icon="🎼")
st.title("🎼 Muschat")
st.caption("Música contemporânea, eletroacústica e técnicas estendidas.")


def _secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except StreamlitSecretNotFoundError:
        return {}


try:
    # snapshot before st.secrets: Streamlit copies secrets into os.environ
    cfg = load_config(dict(os.environ), _secrets())
except MissingApiKeyError:
    st.error(
        "Chave do Gemini não configurada. Defina `GEMINI_API_KEY` num arquivo `.env`, no ambiente "
        'ou em `.streamlit/secrets.toml` (`GEMINI_API_KEY = "sua-chave"`) e recarregue a página.'
    )
    st.stop()


@st.cache_resource
def _client(api_key: str) -> genai.Client:
    retry = types.HttpRetryOptions(attempts=3)
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=90_000, retry_options=retry))


@st.cache_resource
def _retriever(api_key: str, embed_model: str):
    embedder = GeminiEmbedder(_client(api_key).models, embed_model)
    return open_retriever(Path("index"), embedder, embed_model)


def _new_chat():
    return _client(cfg.api_key).chats.create(model=cfg.model)


def _new_conversation():
    st.session_state.chat = _new_chat()
    st.session_state.messages = []
    st.session_state.composition = Composition()


if "chat" not in st.session_state or "composition" not in st.session_state:
    _new_conversation()
else:
    try:
        Composition.refresh_schema(st.session_state.composition)
    except ValueError:
        st.error(
            "Não foi possível atualizar esta composição após recarregar o aplicativo. "
            "O histórico foi preservado; reinicie o aplicativo para tentar novamente."
        )
        st.stop()

st.sidebar.button("Nova conversa", on_click=_new_conversation, width="stretch")


@st.cache_resource(show_spinner=False)
def _lilypond(configured: str | None) -> LilyPondInstallation | None:
    return detect_lilypond(configured)


with st.sidebar.expander("NotaGen — modelo local"):
    notagen_enabled = st.checkbox("Ativar NotaGen", value=cfg.notagen is not None, key="notagen_enabled")
    notagen_python = st.text_input(
        "Python do NotaGen",
        value=(cfg.notagen or (DEFAULT_NOTAGEN_PYTHON, DEFAULT_NOTAGEN_DIR))[0],
        key="notagen_python",
    )
    notagen_dir = st.text_input(
        "Pasta do NotaGen",
        value=(cfg.notagen or (DEFAULT_NOTAGEN_PYTHON, DEFAULT_NOTAGEN_DIR))[1],
        key="notagen_dir",
    )
    st.caption("Configuração desta sessão. Requer pesos instalados e GPU CUDA. O worker opcional reutiliza os pesos entre chamadas.")


def _notagen() -> NotaGenInstallation | str | None:
    key = (notagen_enabled, notagen_python, notagen_dir)
    cache = st.session_state.setdefault("notagen_detection", {})
    if key not in cache:
        cache[key] = detect_notagen(notagen_python, notagen_dir) if notagen_enabled else None
    return cache[key]


def _composer() -> LocalNotaGen | None:
    installation = _notagen()
    if not isinstance(installation, NotaGenInstallation):
        return None
    key = (installation, cfg.notagen_worker, cfg.notagen_idle_timeout)
    previous = st.session_state.get("composer_backend")
    if previous is not None and previous[0] == key:
        return previous[1]
    if previous is not None and isinstance(previous[1], PersistentNotaGen):
        previous[1].close()
    backend = PersistentNotaGen(installation, idle_timeout=cfg.notagen_idle_timeout) if cfg.notagen_worker else LocalNotaGen(installation)
    st.session_state.composer_backend = (key, backend)
    return backend


def _registry(openmusic_enabled: bool = cfg.openmusic_enabled) -> Registry:
    key = (openmusic_enabled, notagen_enabled, notagen_python, notagen_dir,
           os.environ.get("OPENMUSIC"), os.environ.get("LILYPOND"), os.environ.get("CSOUND"), os.environ.get("PATH"))
    cache = st.session_state.setdefault("capability_cache", {})
    if key in cache:
        return cache[key]
    try:
        abjad_version: str | None = version("abjad")
    except PackageNotFoundError:
        abjad_version = None
    registry = detect_registry(
        lilypond=_lilypond(os.environ.get("LILYPOND")),
        openmusic=(detect_openmusic(os.environ.get("OPENMUSIC")) if openmusic_enabled else None),
        abjad_version=abjad_version,
        openmusic_enabled=openmusic_enabled,
        notagen=_notagen(),
    )
    cache[key] = registry
    return registry


if st.sidebar.button("Redetectar ferramentas"):
    previous = st.session_state.pop("composer_backend", None)
    if previous is not None and isinstance(previous[1], PersistentNotaGen):
        previous[1].close()
    st.session_state.pop("capability_cache", None)
    st.session_state.pop("notagen_detection", None)
    st.session_state.pop("export_cache", None)
    _lilypond.clear()
    st.session_state["tool_detection_revision"] = st.session_state.get("tool_detection_revision", 0) + 1

_ICON = {"automatic": "🟢", "file_exchange": "🟡", "manual": "🟠", "unavailable": "⚪"}

modes = ["auto", "python"] + (["openmusic", "hybrid"] if cfg.openmusic_enabled else [])
if notagen_enabled:
    modes.append("notagen")
if st.session_state.get("mode") not in modes:
    st.session_state.mode = "auto"
st.sidebar.radio(
    "Motor",
    modes,
    format_func=MODES.__getitem__,
    key="mode",
    horizontal=True,
    help="Automático escolhe por regras explícitas; os outros são respeitados ou recusados com o motivo.",
)
if st.session_state.mode == "openmusic":
    st.sidebar.caption(
        "OpenMusic assistido: o chat prepara uma tarefa para você executar o patch no OpenMusic "
        "e importar a saída. Orquestração e técnicas sem patch rodam depois em Python/Abjad; "
        "essa cadeia é registrada como Híbrido."
    )
elif st.session_state.mode == "notagen":
    st.sidebar.caption(
        "Este modo exige uma nova geração NotaGen seguida de transformações Python. "
        "Para editar sem regenerar, use Automático ou Python/Abjad."
    )
if notagen_enabled:
    installation = _notagen()
    if isinstance(installation, NotaGenInstallation):
        st.sidebar.success(f"NotaGen instalado: {len(installation.conditions)} condições disponíveis.")
        st.sidebar.caption(
            "Selecione NotaGen + Python para exigir geração pelo modelo local. "
            "A disponibilidade CUDA é verificada na execução."
        )
    else:
        st.sidebar.error(str(installation))
with st.sidebar.expander("Motores e bibliotecas"):
    for cap in _registry().capabilities:
        ver_str = f" {cap.version}" if cap.version else ""
        st.markdown(f"{_ICON[cap.state]} **{cap.name}**{ver_str} · {STATE_LABEL[cap.state]}")
        st.caption(cap.reason)
retriever, index_warning = _retriever(cfg.api_key, cfg.embed_model)
if index_warning:
    _retriever.clear()
    st.sidebar.caption(f"⚠️ {index_warning} Respostas sem base no acervo.")


def _pending_section(c: Composition):
    """Assisted OpenMusic step: pipeline paused awaiting composer output."""
    p = c.pending
    if p is None or p.task is None:
        return
    with st.sidebar.container(border=True):
        st.warning(f"⏳ Aguardando você: etapa {p.step + 1} de {len(p.decision.steps)} no OpenMusic")
        st.caption(f"Saída esperada: {p.task.patch.output_format}. Você também pode colar somente os números no chat.")
        for k, s in enumerate(p.decision.steps):
            state_lbl = "feita" if k < p.step else "aguardando você" if k == p.step else "pendente"
            st.caption(f"{k + 1}. {s.procedure} · {ENGINE_NAME[s.engine]} · {state_lbl}")
        with st.expander("Instruções"):
            st.markdown(p.task.instructions())
        st.download_button(
            "baixar tarefa (.zip)",
            data=p.task.package(),
            file_name=f"{p.task.id}.zip",
            mime="application/zip",
            on_click="ignore",
            width="stretch",
        )
        uploaded = st.file_uploader("saida.txt do OpenMusic", type=["txt"], key=f"saida-{p.task.id}")
        if st.button("Importar e continuar", disabled=uploaded is None, width="stretch", type="primary"):
            assert uploaded is not None
            result = resume(c, uploaded.getvalue(), composer=_composer())
            if result.version:
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": result.summary,
                        "scores": [asdict(version_score_view(result.version))],
                    }
                )
                st.rerun()
            st.error(result.summary)
        st.button("Descartar tarefa", on_click=discard_task, args=(c,), width="stretch")


def _inspection(c: Composition):
    assert c.current is not None
    exp = c.current.experiment
    for k, s in enumerate(exp.get("steps", []), 1):
        st.caption(f"{k}. {s['procedure']} · {ENGINE_NAME.get(s['engine'], s['engine'])} · {s.get('state', s.get('status', ''))}")
    with st.expander("Inspeção técnica"):
        for line in exp.get("decision", {}).get("rationale", []):
            st.markdown(f"- {line}")
        previous = c.versions[c.current.previous].ir if c.current.previous is not None else None
        st.markdown("**Métricas descritivas** (descrição do material, não avaliação estética)")
        st.json(metrics(c.current.ir, previous=previous, violations=exp.get("violations", [])), expanded=False)
        st.json({k: v for k, v in exp.items() if k != "intermediates"}, expanded=False)
        for k, dump in enumerate(exp.get("intermediates", []), 1):
            st.download_button(
                f"intermediário da etapa {k} (.musicxml)",
                data=to_musicxml(ScoreIR.model_validate(dump)),
                file_name=f"step_{k}.musicxml",
                mime="application/vnd.recordare.musicxml+xml",
                key=f"inter-{c.index}-{k}",
                on_click="ignore",
            )


def _json_archive(c: Composition):
    """Save and load the composition (Versions + pending step) in JSON."""
    with st.popover("Arquivo da composição (.json)", width="stretch"):
        st.download_button(
            "salvar composição",
            data=c.to_json,
            file_name="composition.json",
            mime="application/json",
            on_click="ignore",
            width="stretch",
            disabled=not c.versions and c.pending is None,
        )
        st.download_button(
            "dados do experimento",
            data=lambda m=_detected_engines(): experiment_json(c, engines=m),
            file_name="experiment.json",
            mime="application/json",
            on_click="ignore",
            width="stretch",
            disabled=not c.versions,
        )
        st.caption(FORMAT_LOSSES["json"])
        uploaded = st.file_uploader("abrir composição", type=["json"], key="open-composition")
        if uploaded is not None and st.button("Abrir (substitui a atual)", width="stretch"):
            try:
                st.session_state.composition = Composition.from_json(uploaded.getvalue().decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(f"Arquivo recusado: {str(exc).splitlines()[0]}")
            else:
                st.rerun()


def _open_score_file(c: Composition):
    """External MusicXML (Dorico, MuseScore, OpenMusic, NotaGen) becomes a new Version; undo returns to previous."""
    with st.popover("Abrir partitura (MusicXML)", width="stretch"):
        uploaded = st.file_uploader("partitura", type=["musicxml", "xml", "mxl"], key="open-musicxml")
        if uploaded is not None and st.button("Abrir como nova versão", width="stretch"):
            result = open_musicxml(c, uploaded.getvalue(), uploaded.name)
            if result.version is None:
                st.error(result.summary)
            else:
                st.rerun()


def _composition_section(c: Composition):
    _pending_section(c)
    with st.sidebar.expander("Composição", expanded=c.current is not None or c.pending is not None):
        _json_archive(c)
        _open_score_file(c)
        if c.current is None or c.index is None:
            st.caption(
                'Nenhuma composição ainda. Peça, por exemplo: "crie oito compassos para quarteto de cordas '
                'a partir de parciais harmônicos".'
            )
            return
        st.markdown(f"**Versão atual: {c.index + 1}**  \n{c.current.summary}")
        _inspection(c)
        st.button("Desfazer", on_click=c.undo, disabled=c.current.previous is None, width="stretch")
        if c.current.reapplicable:
            _adjust_parameters(c)
        _export_panel(c)
        names = [
            f"{i + 1} · {v.experiment.get('operation', '')} · {v.created_at:%H:%M} · {v.summary.partition(': ')[2][:50]}"
            for i, v in enumerate(c.versions)
        ]
        chosen = st.selectbox("Versões", range(len(names)), index=c.index, format_func=names.__getitem__)
        st.button(
            "Voltar a esta versão",
            on_click=c.checkout,
            args=(chosen,),
            disabled=chosen == c.index,
            width="stretch",
        )
        if len(c.versions) > 1:
            from_idx = st.selectbox(
                "Comparar de",
                range(len(names)),
                index=max(c.index - 1, 0),
                format_func=lambda i: str(i + 1),
            )
            to_idx = st.selectbox("até", range(len(names)), index=c.index, format_func=lambda i: str(i + 1))
            diff = c.diff(from_idx, to_idx)
            st.markdown(
                f"{len(diff.added)} adicionados · {len(diff.removed)} removidos · {len(diff.modified)} alterados"
                if diff
                else "Sem diferenças."
            )
            by_loc = Counter(diff.locations.values())
            for part_id in sorted({p for p, _ in by_loc}):
                st.caption(
                    f"{part_id}: " + ", ".join(f"m.{n} ({k})" for (p, n), k in sorted(by_loc.items()) if p == part_id)
                )


def _detected_engines() -> dict[str, str]:
    """Versions of detected external engines for the experiment JSON."""
    return {
        cap.name: cap.version
        for cap in _registry().capabilities
        if cap.version and cap.state != "unavailable" and cap.name != "Abjad"
    }


def _score_artifacts(ir: ScoreIR, transposed: bool):
    key = (hashlib.sha256(ir.model_dump_json().encode()).hexdigest(), transposed,
           os.environ.get("LILYPOND"), st.session_state.get("tool_detection_revision", 0))
    cache = st.session_state.setdefault("export_cache", {})
    if key not in cache:
        try:
            lilypond = to_lilypond(ir, transposed=transposed)
        except ValueError:
            lilypond = {}
        engraving = _engrave(tuple(lilypond.items()), os.environ.get("LILYPOND"), st.session_state.get("tool_detection_revision", 0)) if lilypond else None
        musicxml = to_musicxml(ir, transposed=transposed)
        cache[key] = (musicxml, to_midi(ir), lilypond, engraving,
                      None if engraving and engraving.pdfs else _svg(musicxml))
        if len(cache) > 16:
            del cache[next(iter(cache))]
    return cache[key]


def _export_panel(c: Composition):
    """Downloads for the current Version, each declaring what the format loses."""
    assert c.current is not None and c.index is not None
    ir, i, name = c.current.ir, c.index, f"composition_v{c.index + 1}"
    engines = _detected_engines()
    with st.popover("Exportar", width="stretch"):
        transposed = st.checkbox("Grade transposta", value=False, key="transposed-score")
        musicxml, midi, lilypond, engraving, svg = _score_artifacts(ir, transposed)
        st.download_button(
            "composição completa (.zip)",
            file_name=f"{name}.zip",
            mime="application/zip",
            data=lambda: version_package(
                c,
                i,
                lilypond=lilypond,
                engraving=engraving,
                svg=svg,
                engines=engines,
                transposed=transposed,
            ),
            on_click="ignore",
            width="stretch",
            type="primary",
        )
        st.caption(
            "PDF/.ly só se o LilyPond gravou; SVG do LilyPond ou, sem ele, do Verovio; MusicXML, MIDI, "
            "JSONs e LOSSES.txt sempre. Cada formato perde o que está descrito abaixo e em LOSSES.txt."
        )
        st.download_button(
            "MusicXML",
            data=musicxml,
            file_name=f"{name}.musicxml",
            mime="application/vnd.recordare.musicxml+xml",
            on_click="ignore",
            width="stretch",
        )
        st.caption(FORMAT_LOSSES["musicxml"])
        st.download_button(
            "MIDI",
            data=midi,
            file_name=f"{name}.mid",
            mime="audio/midi",
            on_click="ignore",
            width="stretch",
        )
        st.caption(FORMAT_LOSSES["midi"])
        if lilypond:
            st.caption(FORMAT_LOSSES["lilypond"])


def _adjust_parameters(c: Composition):
    """Editable operational hypothesis: adjust parameters and re-run the current Version's operation without calling the LLM."""
    assert c.current is not None and c.index is not None
    exp = c.current.experiment
    plan_dict = exp.get("plan", {})
    with st.popover("Ajustar parâmetros e reaplicar", width="stretch"):
        for section in plan_dict.get("sections", []):
            st.write(
                f"{section['name']} - {section['character']} - compassos "
                f"{section['start_measure']}-{section['end_measure']}"
            )
            st.caption(f"{section['material']} - {', '.join(section['techniques'])}")
        for h in plan_dict.get("hypotheses", []):
            st.caption(f"Hipótese: {h}")
        new_params = []
        for k, (proc_name, params) in enumerate(zip(exp.get("procedures", []), exp.get("parameters", []))):
            ranges = CATALOG[proc_name].parameters
            st.markdown(f"`{proc_name}`" + ("" if ranges else " (sem parâmetros)"))
            new_params.append(
                {
                    p: float(
                        st.number_input(
                            f"{p} ({ranges[p].unit or ranges[p].description})",
                            min_value=float(ranges[p].min_value),
                            max_value=float(ranges[p].max_value),
                            value=float(v),
                            step=1.0 if ranges[p].integer else 0.05,
                            key=f"adjust-{c.index}-{k}-{p}",
                        )
                    )
                    for p, v in params.items()
                }
            )
        if st.button("Reaplicar", width="stretch", key=f"reapply-{c.index}"):
            result = reapply(c, new_params, registry=_registry(), composer=_composer())
            (st.toast if result.version else st.error)(result.summary)
            if result.version:
                st.rerun()


@st.cache_data(max_entries=64, show_spinner=False)
def _svg(musicxml: str) -> str | None:
    return render_svg(musicxml)


@st.cache_data(max_entries=16, show_spinner="Gravando a partitura com o LilyPond...")
def _engrave(sources: tuple[tuple[str, str], ...], configured: str | None = None, revision: int = 0) -> EngravingResult:
    return engrave(dict(sources), _lilypond(configured or os.environ.get("LILYPOND")))


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w-]+", "_", text).strip("_")[:60] or "score"


def _render_scores(scores: list[dict], key: str):
    for i, s in enumerate(scores):
        st.caption(s["caption"])
        name = _safe_filename(s["title"])
        engraving = _engrave(tuple(s["lilypond"].items()), os.environ.get("LILYPOND"), st.session_state.get("tool_detection_revision", 0)) if s.get("lilypond") else None
        if engraving and engraving.svgs:
            for page in engraving.svgs:
                st.image(page, width="stretch")
        elif svg := _svg(s["musicxml"]):
            st.image(svg, width="stretch")
        else:
            st.code(s["musicxml"], language="xml")
        if engraving and engraving.warning:
            st.caption(f"⚠️ {engraving.warning}")
        if s.get("lilypond"):
            columns = st.columns(2)
            ly_zip = io.BytesIO()
            with zipfile.ZipFile(ly_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for src, text in s["lilypond"].items():
                    z.writestr(f"{name}_{_safe_filename(src)}.ly", text)
            columns[0].download_button(
                ".ly da grade e das partes (.zip)",
                data=ly_zip.getvalue(),
                file_name=f"{name}_lilypond.zip",
                mime="application/zip",
                key=f"ly-{key}-{i}",
                on_click="ignore",
            )
            for k, (src, pdf) in enumerate((engraving.pdfs if engraving else {}).items()):
                columns[(k + 1) % 2].download_button(
                    f"PDF {'da grade' if src in ('score', 'grade') else src}",
                    data=pdf,
                    file_name=f"{name}_{_safe_filename(src)}.pdf",
                    mime="application/pdf",
                    key=f"pdf-{key}-{i}-{k}",
                    on_click="ignore",
                )
        st.download_button(
            "baixar .musicxml",
            data=s["musicxml"],
            file_name=f"{name}.musicxml",
            mime="application/vnd.recordare.musicxml+xml",
            key=f"dl-{key}-{i}",
            on_click="ignore",
        )


def _render_message(msg: dict, key: str):
    st.markdown(msg["content"])
    _render_scores(msg.get("scores") or [], key)
    if analysis := msg.get("analysis"):
        with st.expander("Raciocínio (análise interna e trechos do acervo)"):
            st.json(analysis)
            excerpts = msg.get("excerpts") or []
            st.markdown(f"**Trechos recuperados:** {len(excerpts) or 'nenhum'}")
            for e in excerpts:
                st.markdown(f"**{e['source']}** — {e['location']}  \n`score {e['score']} · {' + '.join(e['origin'])}`")
                st.caption(e["text"][:400] + ("…" if len(e["text"]) > 400 else ""))


for n, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        _render_message(msg, str(n))

if question := st.chat_input("Pergunte sobre técnicas estendidas, eletroacústica..."):
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Pensando..."):
                reply = respond(
                    question,
                    generator=_client(cfg.api_key).models,
                    chat=st.session_state.chat,
                    retriever=retriever,
                    model=cfg.model,
                    history_messages=cfg.history_messages,
                    composition=st.session_state.composition,
                    mode=st.session_state.mode,
                    registry=_registry(),
                    composer=_composer(),
                )
        except Exception as exc:
            if (err := error_message(exc)) is None:
                raise
            st.error(err)
        else:
            if not reply.final:
                st.warning("O modelo não devolveu texto para essa pergunta. Tente reformular.")
                st.stop()
            msg = {
                "role": "assistant",
                "content": reply.final,
                "analysis": reply.analysis,
                "excerpts": [{k: v for k, v in asdict(e).items() if k != "musicxml"} for e in reply.excerpts],
                "scores": [asdict(s) for s in reply.scores],
            }
            _render_message(msg, str(len(st.session_state.messages) + 1))
            st.session_state.messages += [{"role": "user", "content": question}, msg]

_composition_section(st.session_state.composition)

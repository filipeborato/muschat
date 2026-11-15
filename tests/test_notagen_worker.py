"""Persistent NotaGen worker contract with a lightweight process instead of neural inference."""

import sys
from pathlib import Path
from muschat.notagen import NotaGenInstallation, PersistentNotaGen


def _worker(tmp_path, monkeypatch, crash=False, idle_timeout=5, slow=False):
    import muschat.notagen as notagen

    service = Path("services/notagen_generate.py").resolve()
    script = tmp_path / "worker.py"
    script.write_text(
        f"""import sys, os, runpy, time
from pathlib import Path
count = 0
def render(request):
    global count
    count += 1
    Path("started").write_text("ready")
    if {slow!r}: time.sleep(30)
    Path(request['output']).write_text(str(os.getpid()) + ':' + str(count))
if sys.argv[1] == '--worker':
    if {crash!r}: sys.exit(1)
    runpy.run_path({str(service)!r})['serve_requests'](render, float(sys.argv[3]))
else:
    Path(sys.argv[5]).write_text('fallback')
""",
        encoding="utf-8",
    )
    (tmp_path / "gradio").mkdir()
    monkeypatch.setattr(notagen, "SCRIPT_PATH", script)
    return PersistentNotaGen(
        NotaGenInstallation(sys.executable, str(tmp_path), ("A_B_C",)),
        idle_timeout=idle_timeout,
        timeout=5,
    )


def test_worker_reuses_process_for_consecutive_sections(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch)
    try:
        first = worker.generate("A_B_C", 2, 1).decode()
        second = worker.generate("A_B_C", 2, 2).decode()
        assert first.split(":")[0] == second.split(":")[0]
        assert first.endswith(":1") and second.endswith(":2")
    finally:
        worker.close()


def test_inactive_worker_falls_back_after_idle_timeout(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch, idle_timeout=0.2)
    try:
        worker.generate("A_B_C", 2, 1)
        worker._process.wait(timeout=3)
        assert worker.generate("A_B_C", 2, 2) == b"fallback"
    finally:
        worker.close()


def test_worker_start_failure_falls_back_transparently(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch, crash=True)
    try:
        assert worker.generate("A_B_C", 2, 1) == b"fallback"
    finally:
        worker.close()


def test_explicit_close_cancels_active_request_without_starting_fallback(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    import time
    import pytest

    worker = _worker(tmp_path, monkeypatch, slow=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.generate, "A_B_C", 2, 1)
        deadline = time.monotonic() + 5
        while (
            not (tmp_path / "gradio" / "started").exists()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        try:
            assert (tmp_path / "gradio" / "started").exists()
        finally:
            worker.close()
        with pytest.raises(ValueError, match="closed"):
            future.result(timeout=5)

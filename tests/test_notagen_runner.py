"""Bounded NotaGen generation without loading weights or requiring a GPU."""

from types import SimpleNamespace
import pytest
from services.notagen_generate import generate_measures


def test_generation_stops_before_producing_an_unrequested_measure():
    generated = []
    rows = [
        "[r:0/100][V:1]C4|[V:2]E4|\n",
        "[r:1/99][V:1]D4|[V:2]F4|\n",
        "[r:2/98][V:1]E4|[V:2]G4|\n",
    ]

    def inference_patch(*args):
        print("M:4/4\nL:1/4\nV:1\nV:2\nK:C")
        for row in rows:
            for char in row:
                print(char, end="")
            generated.append(row)
        raise AssertionError("must stop generation before completing the third measure")

    def rest_unreduce(lines):
        assert "[V:1]C4|[V:2]E4|" in "".join(lines)
        assert "[V:1]D4|[V:2]F4|" in "".join(lines)
        assert "[r:2/" not in "".join(lines)
        return lines

    module = SimpleNamespace(
        inference_patch=inference_patch, rest_unreduce=rest_unreduce
    )
    score = generate_measures(module, "Classical", "Mozart", "Chamber", 2)
    assert len(generated) == 2
    assert score.startswith("X:1\n") and "V:1" in score and "V:2" in score


def test_natural_model_completion_before_limit_is_preserved():
    def inference_patch(*args):
        return "X:1\nM:4/4\nK:C\nC4|]\n"

    module = SimpleNamespace(inference_patch=inference_patch)
    assert generate_measures(module, "Classical", "Mozart", "Keyboard", 4).endswith(
        "C4|]\n"
    )


@pytest.mark.parametrize("count", [0, -1, 17])
def test_measure_budget_rejects_counts_outside_the_plan_limits(count):
    with pytest.raises(ValueError, match="1 to 16"):
        generate_measures(SimpleNamespace(), "Classical", "Mozart", "Keyboard", count)


def test_musicxml_input_separates_voices_without_combining_their_durations():
    from services.notagen_generate import to_voice_blocks

    abc = "X:1\nM:4/4\nL:1/4\nV:1 clef=treble\nV:2 clef=bass\nK:C\n[V:1]C4|[V:2]E4|\n[V:1]D4|[V:2]F4|\n"
    result = to_voice_blocks(abc)
    assert "V:1 clef=treble\nC4|D4|" in result
    assert "V:2 clef=bass\nE4|F4|" in result
    assert "[V:" not in result


def test_context_rollover_diagnostic_does_not_split_music_row():
    def inference_patch(*args):
        print("M:4/4\nL:1/4\nV:1\nV:2\nK:C")
        print("[r:0/100][V:1]C", end="")
        print("Stream generating...")
        print("4|[V:2]E4|")
        print("[r:1/99][V:1]D4|[V:2]F4|")
        raise AssertionError("must stop at the next measure")

    module = SimpleNamespace(
        inference_patch=inference_patch, rest_unreduce=lambda lines: lines
    )
    result = generate_measures(module, "Classical", "Mozart", "Chamber", 1)
    assert "Stream generating" not in result
    assert "V:1\nC4|" in result and "V:2\nE4|" in result


def test_worker_loads_native_runtime_before_starting_ipc_readers(tmp_path, monkeypatch):
    import services.notagen_generate as runner

    order = []
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", str(tmp_path), "1"])
    monkeypatch.setattr(runner, "load_runtime", lambda directory: order.append("runtime"))
    monkeypatch.setattr(runner, "serve_requests", lambda render, idle: order.append("ipc"))
    runner.main()
    assert order == ["runtime", "ipc"]

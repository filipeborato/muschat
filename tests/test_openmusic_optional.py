"""Unit tests for explicit OpenMusic opt-in (`MUSCHAT_OPENMUSIC=1`) in canonical English modules."""

from pathlib import Path

import pytest

from muschat.config import load_config
from muschat.openmusic import OmInstallation
from muschat.plan import Operation, Plan
from muschat.registry import detect_capabilities
from muschat.router import NoRouteError, route


def test_config_requires_explicit_openmusic_activation():
    assert not load_config({"GEMINI_API_KEY": "test", "OPENMUSIC": "om.exe"}, {}).openmusic_enabled
    assert load_config({"GEMINI_API_KEY": "test"}, {"MUSCHAT_OPENMUSIC": "1"}).openmusic_enabled


def test_disabled_openmusic_does_not_query_externals_and_allows_python_routing():
    def forbidden(name: str):
        raise AssertionError(name)

    reg = detect_capabilities(
        lilypond=None,
        openmusic=OmInstallation(Path("om.exe"), "8.0"),
        openmusic_enabled=False,
        which=forbidden,
    )
    assert reg.capability("OpenMusic") is None
    plan = Plan(goal="test", operations=[Operation(procedure="harmonic_series")])
    decision = route(plan, "auto", reg)
    assert [s.engine for s in decision.steps] == ["python"]
    with pytest.raises(NoRouteError, match="MUSCHAT_OPENMUSIC=1"):
        route(plan, "openmusic", reg)

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.rag_chatbot import timing


@pytest.mark.parametrize("trace_setting", [None, "0"])
def test_streamlit_enables_console_node_progress_unless_disabled(
    monkeypatch, capsys, trace_setting
):
    monkeypatch.delenv("BOKJI_TRACE", raising=False)
    if trace_setting is not None:
        monkeypatch.setenv("BOKJI_TRACE", trace_setting)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["view"] = "login"
    app.run(timeout=30)
    assert not app.exception
    monkeypatch.setattr(timing, "TIMER", timing.PhaseTimer())
    capsys.readouterr()
    assert timing.timed_node("policy_search", lambda: 42)() == 42
    output = capsys.readouterr().out
    if trace_setting is None:
        assert "N4 policy_search" in output
        assert "->" in output and "초)" in output
    else:
        assert output == ""
    assert timing.TIMER.path()[0][0] == "policy_search"
    assert timing.TIMER.summary()[0]["count"] == 1
    assert timing.TIMER.current() is None


def test_node_failure_propagates_and_still_records_elapsed_time(monkeypatch, capsys):
    monkeypatch.setenv("BOKJI_TRACE", "1")
    monkeypatch.setattr(timing, "TIMER", timing.PhaseTimer())

    def fail():
        raise RuntimeError("test failure")

    with pytest.raises(RuntimeError, match="test failure"):
        timing.timed_node("policy_search", fail)()
    output = capsys.readouterr().out
    assert "종료" in output and "완료" not in output
    assert "test failure" not in output
    assert timing.TIMER.path()[0][1] >= 0
    assert timing.TIMER.current() is None

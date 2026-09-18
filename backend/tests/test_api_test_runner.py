"""Offline runner boundaries; the probes never send real network traffic."""

import base64
import os
from pathlib import Path
import sys

import pytest

from scripts.run_api_tests import OfflineAccessError, isolated_environment, offline_guard


def test_environment_discards_credentials_and_pytest_injection(tmp_path, monkeypatch):
    environment = {"PATH": os.environ.get("PATH", ""), "AUTH_DB_URL": "mysql://synthetic",
                   "AUTH_ENC_KEY": "old", "HF_TOKEN": "synthetic", "RUNPOD_API_KEY": "synthetic",
                   "UNRECOGNIZED_PROVIDER_SECRET": "synthetic", "PYTEST_ADDOPTS": "--bad-option",
                   "PYTEST_PLUGINS": "unwanted_plugin", "PYTHONPATH": "unwanted_path"}
    monkeypatch.setattr(os, "environ", environment)
    isolated_environment(tmp_path)
    assert set(environment).isdisjoint({"AUTH_DB_URL", "HF_TOKEN", "RUNPOD_API_KEY",
        "UNRECOGNIZED_PROVIDER_SECRET", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"})
    assert environment["AUTH_DB_PATH"] == str(tmp_path / "auth.db")
    key = environment["AUTH_ENC_KEY"]
    assert len(base64.urlsafe_b64decode(key)) == 32
    isolated_environment(tmp_path)
    assert environment["AUTH_ENC_KEY"] != key
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"


@pytest.mark.parametrize("event,args", [
    ("socket.connect", (None, ("127.0.0.1", 1))),
    ("socket.connect", (None, ("198.51.100.1", 443))),
    ("socket.getaddrinfo", ("provider.invalid", 443, 0, 0, 0)),
    ("socket.sendto", (None, ("198.51.100.1", 53))),
    ("subprocess.Popen", ("unused", [], None, {})),
])
def test_network_and_process_probes_escape_application_fallback(tmp_path, event, args):
    guard = offline_guard(tmp_path, tmp_path / "reports")
    with pytest.raises(OfflineAccessError):
        try:
            guard(event, args)
        except Exception:
            pytest.fail("A normal application fallback must not swallow the offline boundary")


@pytest.mark.parametrize("name", [".env", ".env.production"])
def test_dotenv_reads_are_denied_even_inside_temporary_directory(tmp_path, name):
    guard = offline_guard(tmp_path, tmp_path / "reports")
    with pytest.raises(OfflineAccessError):
        guard("open", (str(tmp_path / name), "r", 0))


def test_database_and_write_paths_stay_isolated(tmp_path):
    data = tmp_path / "data"
    reports = tmp_path / "reports"
    guard = offline_guard(data, reports)
    for path in (":memory:", data / "test.db"):
        guard("sqlite3.connect", (path,))
    guard("open", (reports / "junit.xml", "w", os.O_WRONLY))
    for path in (tmp_path / "existing.db", data / ".." / "existing.db", reports / "report.db"):
        with pytest.raises(OfflineAccessError):
            guard("sqlite3.connect", (path,))
    with pytest.raises(OfflineAccessError):
        guard("open", (tmp_path / "existing.db", "w", os.O_WRONLY))


def test_runner_installs_guard_and_preserves_pytest_failure_exit(tmp_path, monkeypatch):
    from scripts import run_api_tests as runner
    import pytest as pytest_module

    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(runner, "REPORTS", tmp_path / "reports")
    observed = []
    def failing_pytest(options):
        assert len(hooks) == 1
        with pytest.raises(OfflineAccessError):
            hooks[0]("socket.connect", (None, ("198.51.100.1", 443)))
        observed.append(Path(os.environ["AUTH_DB_PATH"]).parent)
        assert "--junitxml" in options and "--basetemp" in options
        assert options[0] == "backend/tests/test_auth_api.py"
        return pytest_module.ExitCode.TESTS_FAILED
    monkeypatch.setattr(pytest_module, "main", failing_pytest)
    assert runner.main(["backend/tests/test_auth_api.py", "-k", "signup"]) == 1
    assert not observed[0].exists()  # TemporaryDirectory cleanup also on test failure


@pytest.mark.parametrize("missing_module", ["dotenv", "pytest"])
def test_startup_import_failure_removes_only_stale_reports(tmp_path, monkeypatch, missing_module):
    import builtins
    from scripts import run_api_tests as runner

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "junit.xml").write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    (reports / "pytest.log").write_text("1 passed\n", encoding="utf-8")
    sibling = reports / "keep.txt"
    sibling.write_text("unrelated report", encoding="utf-8")
    monkeypatch.setattr(runner, "REPORTS", reports)
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    original_import = builtins.__import__
    temporary = []

    def fail_import(name, *args, **kwargs):
        if name == missing_module:
            temporary.append(Path(os.environ["AUTH_DB_PATH"]).parent)
            raise ModuleNotFoundError(f"synthetic startup failure: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    # The entry point must propagate startup failure, never return a successful exit.
    with pytest.raises(ModuleNotFoundError, match=f"synthetic startup failure: {missing_module}"):
        runner.main(["backend/tests/test_auth_api.py"])
    assert len(temporary) == 1 and not temporary[0].exists()
    assert sibling.read_text(encoding="utf-8") == "unrelated report"
    assert not (reports / "junit.xml").exists()
    assert not (reports / "pytest.log").exists()

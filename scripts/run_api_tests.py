"""Run offline API contracts with pytest; no server, credentials or model downloads."""

from __future__ import annotations

import argparse
import base64
import os
import platform
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "api-tests"


class OfflineAccessError(BaseException):
    """Escape application fallback catches so unintended I/O fails the test."""


def isolated_environment(directory: Path) -> None:
    # Retain only OS essentials; even unknown inherited provider credentials vanish.
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE"}
    retained = {k: v for k, v in os.environ.items() if k.upper() in keep}
    os.environ.clear()
    os.environ.update(retained)
    for key in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "APPDATA",
                "LOCALAPPDATA", "HF_HOME", "XDG_CACHE_HOME", "TORCH_HOME"):
        os.environ[key] = str(directory)
    os.environ.update(
        AUTH_DB_PATH=str(directory / "auth.db"),
        BOKJI_LOG_DIR=str(directory / "logs"),
        AUTH_ENC_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
        PYTHON_DOTENV_DISABLED="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        ANONYMIZED_TELEMETRY="False", LANGCHAIN_TRACING_V2="false",
    )


def offline_guard(directory: Path, reports: Path):
    def inside(path, root):
        return Path(os.fsdecode(path)).resolve().is_relative_to(root)

    def writable(path):
        return inside(path, directory) or inside(path, reports)

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path, mode, flags = args
            name = Path(os.fsdecode(path)).name
            if name == ".env" or name.startswith(".env."):
                raise OfflineAccessError("Offline tests do not read dotenv files")
            writing = any(c in (mode or "") for c in "wax+") or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC
            )
            if writing and not writable(path):
                raise OfflineAccessError("Offline tests write only temporary data and reports")
        if event == "sqlite3.connect" and args[0] != ":memory:" and not inside(args[0], directory):
            raise OfflineAccessError("Offline tests use temporary SQLite only")
        # Do not interpret unlink/rmdir paths: POSIX cleanup uses paths relative to dir_fd.
        if event == "os.mkdir" and not writable(args[0]):
            raise OfflineAccessError("Offline tests modify only temporary data and reports")
        if event in ("subprocess.Popen", "os.system", "os.fork", "os.posix_spawn"):
            raise OfflineAccessError("Offline tests do not launch external processes")
        if event in ("socket.connect", "socket.sendto", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
            # Windows asyncio creates an internal loopback socketpair for wakeups.
            # No application loopback, provider, DNS or DB connection is permitted.
            frame = sys._getframe(1)
            while frame:
                if (frame.f_globals.get("__name__") == "socket"
                        and frame.f_code.co_name in ("socketpair", "_fallback_socketpair")):
                    return
                frame = frame.f_back
            raise OfflineAccessError("Offline tests block network access")
    return audit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*", help="backend test paths or pytest node IDs")
    parser.add_argument("-k", default="", help="pytest name expression")
    parser.add_argument("-x", action="store_true", help="stop at the first failure")
    parser.add_argument("-v", action="store_true", help="show individual test names")
    args = parser.parse_args(argv)
    tests = args.tests or ["backend/tests"]
    for selector in tests:
        path = (ROOT / selector.split("::", 1)[0]).resolve()
        if not path.is_relative_to(ROOT / "backend" / "tests"):
            parser.error("Select tests under backend/tests only")

    os.chdir(ROOT)
    # Windows의 보고서용 OS 정보 조회는 격리 전에 캐시한다.
    platform.uname()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    REPORTS.mkdir(parents=True, exist_ok=True)
    for name in ("junit.xml", "pytest.log"):
        (REPORTS / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="bokji-api-tests-") as temporary:
        directory = Path(temporary).resolve()
        isolated_environment(directory)
        tempfile.tempdir = str(directory)  # TemporaryDirectory cached the original OS temp path.
        sys.addaudithook(offline_guard(directory, REPORTS.resolve()))
        # Pydantic Settings also reads dotenv_values directly; patch before imports.
        import dotenv
        import dotenv.main
        dotenv.load_dotenv = dotenv.main.load_dotenv = lambda *a, **k: False
        dotenv.dotenv_values = dotenv.main.dotenv_values = lambda *a, **k: {}
        import pytest

        print("OFFLINE API tests: temporary SQLite/key; dotenv/network blocked; no live providers", flush=True)
        print(f"Python {sys.version.split()[0]} | JUnit: {REPORTS / 'junit.xml'}", flush=True)
        options = [*tests, "-v" if args.v else "-q", "-ra", "--tb=short",
                   "-p", "no:cacheprovider", "--basetemp", str(directory / "pytest"),
                   "--junitxml", str(REPORTS / "junit.xml"),
                   "--log-file", str(REPORTS / "pytest.log")]
        if args.k:
            options += ["-k", args.k]
        if args.x:
            options += ["-x"]
        return int(pytest.main(options))


if __name__ == "__main__":
    raise SystemExit(main())

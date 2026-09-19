"""backend/tests/ 공통 fixture.

레포 루트 conftest.py처럼 이 파일도 sys.path를 잡아준다(레포 루트 +
``src``). 인증 테스트는 실제 ``auth.service``를 그대로 호출하되, 매 테스트마다
격리된 임시 SQLite DB(``db_path``)를 쓴다 - 기존 ``tests/test_auth*.py``와
동일한 패턴이다. 채팅 테스트는 무거운 LangGraph 실행 자체를 다시 검증하지
않고(``tests/test_graph_builder.py``가 이미 담당), ``chat_adapter``/
``followup_adapter``의 호출부만 monkeypatch해서 라우팅/스키마/에러코드만
확인한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def isolated_auth_db(tmp_path, monkeypatch) -> Path:
    """각 테스트가 독립된 SQLite auth DB를 쓰게 한다(기존 회원 데이터와 무관)."""

    from src.rag_chatbot.auth.crypto import generate_key

    db_path = tmp_path / "auth-test.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db_path))
    monkeypatch.delenv("AUTH_DB_URL", raising=False)
    # 테스트 프로세스 전체가 같은 PII 암호화 키를 쓰게 고정(매 테스트 새
    # dev 키가 생성되며 .runtime/에 흔적을 남기는 것을 방지). Fernet 사용 시
    # 형식 검증을 통과해야 하므로 crypto.generate_key()로 유효한 키를 만든다.
    monkeypatch.setenv("AUTH_ENC_KEY", generate_key())
    return db_path


@pytest.fixture()
def client(isolated_auth_db) -> TestClient:
    from backend.app.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_process_memory_session_stores():
    """세션 저장소는 프로세스 전역 싱글턴이라 테스트 간 격리를 위해 매번 비운다."""

    from backend.app.session_store.auth_session import auth_session_store
    from backend.app.session_store.chat_session import chat_session_store

    auth_session_store._sessions.clear()  # noqa: SLF001
    chat_session_store._sessions.clear()  # noqa: SLF001
    yield
    auth_session_store._sessions.clear()  # noqa: SLF001
    chat_session_store._sessions.clear()  # noqa: SLF001

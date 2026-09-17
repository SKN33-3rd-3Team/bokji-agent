"""세션별 재개/삭제 순서와 예외 시 잠금 해제를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock
import weakref

import pytest

from backend.app.core.errors import ApiError
from backend.app.services import chat_adapter


class ObservedLock:
    """sleep 없이 두 번째 작업이 실제 잠금을 기다리는 지점을 관측한다."""

    def __init__(self):
        self.lock = Lock()
        self.waiting = Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.waiting.set()
            assert self.lock.acquire(timeout=5)

    def __exit__(self, *args):
        self.lock.release()


@pytest.mark.parametrize("first", ["resume", "delete"])
def test_resume_and_delete_are_serialized(monkeypatch, first):
    store = chat_adapter.chat_session_store
    store.create("session", user_id=1)
    record = store.get("session", user_id=1)
    lock = ObservedLock()
    record.operation_lock = lock
    reference = weakref.ref(record)
    entered, release = Event(), Event()
    checkpoints = {"session": "initial"}
    events = []

    def resume(sid, message):
        events.append("resume")
        if first == "resume":
            entered.set()
            assert release.wait(5)
        checkpoints[sid] = "resumed"
        return {"session_id": sid, "status": "answered"}

    def delete(sid):
        events.append("delete")
        if first == "delete":
            entered.set()
            assert release.wait(5)
        checkpoints.pop(sid, None)

    monkeypatch.setattr(chat_adapter, "answer_followup", resume)
    monkeypatch.setattr(chat_adapter, "get_graph", lambda: SimpleNamespace(
        checkpointer=SimpleNamespace(delete_thread=delete),
    ))
    calls = {
        "resume": lambda: chat_adapter.continue_chat("session", "답변", user_id=1),
        "delete": lambda: chat_adapter.delete_chat_session("session", user_id=1),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(calls[first])
        try:
            assert entered.wait(5)
            second = "delete" if first == "resume" else "resume"
            second_result = pool.submit(calls[second])
            assert lock.waiting.wait(5)
            assert events == [first]
            # 다른 세션은 장시간 그래프 실행 중에도 접근할 수 있다.
            store.create("independent", user_id=2)
            with store.locked("independent", user_id=2) as other:
                assert other is not None
        finally:
            release.set()
        first_result.result(timeout=5)
        if first == "delete":
            with pytest.raises(ApiError) as caught:
                second_result.result(timeout=5)
            assert caught.value.status_code == 404
        else:
            second_result.result(timeout=5)
    assert "session" not in checkpoints
    assert store.get("session", user_id=1) is None
    chat_adapter.delete_chat_session("session", user_id=1)  # 멱등 삭제
    assert events == (["resume", "delete"] if first == "resume" else ["delete"])
    # 대기 작업까지 끝나면 잠금을 가진 레코드를 보관하는 별도 registry가 없다.
    if first == "resume":
        del record
        assert reference() is None


@pytest.mark.parametrize("operation", ["resume", "delete"])
def test_operation_error_releases_session_lock(monkeypatch, operation):
    store = chat_adapter.chat_session_store
    store.create("session", user_id=1)
    record = store.get("session", user_id=1)
    monkeypatch.setattr(chat_adapter, "answer_followup", Mock(side_effect=RuntimeError("failed")))
    checkpointer = Mock()
    checkpointer.delete_thread.side_effect = RuntimeError("failed")
    monkeypatch.setattr(chat_adapter, "get_graph", lambda: SimpleNamespace(checkpointer=checkpointer))
    with pytest.raises((ApiError, RuntimeError)):
        if operation == "resume":
            chat_adapter.continue_chat("session", "답변", user_id=1)
        else:
            chat_adapter.delete_chat_session("session", user_id=1)
    assert record.operation_lock.acquire(blocking=False)
    record.operation_lock.release()
    assert store.get("session", user_id=1) is record
    checkpointer.delete_thread.side_effect = None
    chat_adapter.delete_chat_session("session", user_id=1)
    assert store.get("session", user_id=1) is None

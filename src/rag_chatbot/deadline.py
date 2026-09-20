"""동기 노드의 호출자 대기 한도. 실행 중인 SDK 스레드를 강제 종료하지 않는다."""

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from copy import deepcopy
from threading import BoundedSemaphore, Event, RLock
from time import monotonic


class NodeDeadlineExceeded(TimeoutError):
    """일반 LLMCallError 폴백으로 처리하면 안 되는 노드 실행 한도 초과."""


_execution: ContextVar[Event | None] = ContextVar("graph_execution_abort", default=None)
_deadline: ContextVar["Deadline | None"] = ContextVar("node_deadline", default=None)


class Deadline:
    def __init__(self, seconds: float | None):
        self.expires = monotonic() + seconds if seconds is not None else None
        self.abort = _execution.get()
        self.closed = False
        self.lock = RLock()

    def remaining(self, limit: float | None = None) -> float | None:
        with self.lock:
            left = self.expires - monotonic() if self.expires is not None else None
            if self.closed or (self.abort is not None and self.abort.is_set()) or (left is not None and left <= 0):
                if self.abort is not None:
                    self.abort.set()
                raise NodeDeadlineExceeded("LLM graph node execution deadline exceeded")
            return limit if left is None else left if limit is None else min(limit, left)

    @contextmanager
    def guard(self):
        # 짧은 캐시 쓰기와 만료를 같은 잠금으로 직렬화한다. I/O는 이 안에서 하지 않는다.
        with self.lock:
            self.remaining()
            yield

    def close(self):
        with self.lock:
            self.closed = True


def remaining_timeout(seconds: float) -> float:
    deadline = _deadline.get()
    return deadline.remaining(seconds) if deadline is not None else seconds


def check_deadline() -> None:
    deadline = _deadline.get()
    if deadline is not None:
        deadline.remaining()


@contextmanager
def active_write():
    deadline = _deadline.get()
    if deadline is None:
        yield
    else:
        with deadline.guard():
            yield


@contextmanager
def graph_execution():
    token = _execution.set(Event())
    try:
        yield
    finally:
        _execution.reset(token)


class BoundedExecutor:
    """실행+대기 작업 수를 제한한다. 만료된 호출마다 새 풀/스레드를 만들지 않는다."""
    def __init__(self, workers: int, name: str):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self.slots = BoundedSemaphore(workers)

    def submit(self, func, *args):
        while not self.slots.acquire(timeout=remaining_timeout(0.02)):
            check_deadline()
        try:
            check_deadline()
            future = self.pool.submit(copy_context().run, func, *args)
        except BaseException:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        return future


def wait_for(future):
    # 다른 노드가 만료되면 같은 invoke의 형제 노드도 기다림을 끝낸다.
    # future.result의 TimeoutError와 작업 자체의 TimeoutError를 혼동하지 않는다.
    while not wait([future], timeout=remaining_timeout(0.02)).done:
        check_deadline()
    check_deadline()
    return future.result()


# ponytail: 프로세스당 최대16 노드/16 prefetch 작업. 막힌 SDK는 슬롯을 점유하며
# 신규 요청은 자기 예산 안에서만 기다린다. 강제 회수에는 별도 프로세스 격리가 필요하다.
_nodes = BoundedExecutor(16, "graph-node")


def run_node(func, args, kwargs, *, seconds: float | None):
    if seconds is None and _execution.get() is None:
        return func(*args, **kwargs)
    deadline = Deadline(seconds)
    token = _deadline.set(deadline)
    future = None
    try:
        def invoke():
            check_deadline()
            result = func(*deepcopy(args), **deepcopy(kwargs))
            check_deadline()
            return result

        future = _nodes.submit(invoke)
        return wait_for(future)
    except NodeDeadlineExceeded:
        if deadline.abort is not None:
            deadline.abort.set()
        raise
    finally:
        deadline.close()
        if future is not None:
            future.cancel()  # 실행 중이면 False. 종료/강제취소를 기다리지 않는다.
        _deadline.reset(token)

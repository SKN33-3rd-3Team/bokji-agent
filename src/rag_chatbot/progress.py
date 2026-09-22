"""요청 한 건의 진행 상황 추적 - 프론트엔드 진행률 표시용.

왜 ``timing.TIMER``를 그대로 쓰지 않는가: ``PhaseTimer``는 모듈 전역 인스턴스
하나라 여러 요청이 같은 프로세스에서 동시에 돌면 기록이 섞인다(timing.py
모듈 docstring의 "한계" 절). Streamlit 화면은 "한 사용자가 한 요청만 돌린다"는
전제로 그 한계를 안고 ``TIMER.path()``로 막대를 그렸지만, FastAPI는 여러
사용자의 요청을 같은 프로세스(스레드풀)에서 동시에 처리하므로 같은 방식을
쓰면 A의 진행률 막대에 B가 돌린 노드가 섞여 들어간다.

그래서 진행 상황만 **요청 키별로** 따로 모은다. 키는 ``GraphState["query_id"]``
(== ``session_id``)다 - 노드가 받는 state에 항상 들어 있어서(builder.run_graph)
컨텍스트 전파(contextvars)가 스레드풀 경계를 넘는지 여부에 의존하지 않는다.

기록하는 쪽: ``timing.timed_node``(노드 시작/종료) + 요청을 시작한 쪽.
읽는 쪽: ``backend/app/api/v1/chat.py``의 진행률 조회 API -> 프론트 진행 막대.

이 모듈은 ``timing``을 import하지 않는다(반대 방향 import가 생기므로 순환이
된다). 전체 단계 수(``EXPECTED_NODE_COUNT``)는 요청을 시작하는 쪽이
``start(total_steps=...)``로 넘겨준다.

한계(숨기지 않음): 진행률은 **어림값**이다. 그래프가 조건부 분기를 타서 실제
노드 수는 끝나봐야 알기 때문에, 끝나기 전에는 95%를 넘기지 않는다
(streamlit_ui/pages/chat.py의 진행 막대와 같은 규칙).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

# 끝난 요청을 바로 지우면 프론트의 마지막 폴링이 "없음"을 받아 막대가 100%에
# 닿지 못한 채 사라진다. 잠깐 남겨 두고 그 뒤에 치운다.
_RETENTION_SECONDS = 60.0
_MAX_RECORDS = 256

# 끝나기 전 진행률 상한. 조건부 분기 때문에 실제 노드 수는 끝나봐야 알므로,
# 분모가 모자라 100%에 먼저 닿아도 여기서 멈춘다.
_RUNNING_CEILING = 0.95

RUNNING = "running"
DONE = "done"
FAILED = "failed"

_STARTING_MESSAGE = "상담을 준비하고 있어요"

# 노드 이름 -> 사용자에게 보여줄 진행 문구. ``timing.NODE_LABELS``는 개발자가
# 로그에서 읽는 설명("하드 게이트 슬롯이 다 찼는지 판정")이라 화면에 그대로
# 내보내면 내부 용어가 새어 나간다. 화면에는 같은 단계를 사용자 말로 옮긴 이
# 표를 쓴다(노드 번호/내부 이름은 내보내지 않는다).
_STEP_MESSAGES: dict[str, str] = {
    "slot_parser": "말씀하신 내용에서 필요한 정보를 정리하고 있어요",
    "slot_completeness_gate": "빠진 정보가 없는지 확인하고 있어요",
    "general_law_reference_search": "참고할 법령을 찾고 있어요",
    "request_missing_slots": "추가로 여쭤볼 내용을 정리하고 있어요",
    "policy_search": "받을 수 있는 지원 제도를 찾고 있어요",
    "claim_plan": "정책 원문에서 확인할 조건을 뽑고 있어요",
    "document_verification": "조건이 원문에 실제로 있는지 확인하고 있어요",
    "evidence_gate": "근거가 충분한지 확인하고 있어요",
    "targeted_law_search": "관련 법령을 확인하고 있어요",
    "eligibility_verdict": "자격 충족 여부를 판정하고 있어요",
    "benefit_calculator": "받을 수 있는 지원금을 계산하고 있어요",
    "request_calc_info": "지원금 계산에 필요한 정보를 정리하고 있어요",
    "duplicate_benefit": "다른 제도와 함께 받을 수 있는지 확인하고 있어요",
    "result_assembly": "찾은 제도를 정리하고 있어요",
    "answer_generation": "안내 문장을 작성하고 있어요",
    "final_verification": "안내 내용을 마지막으로 점검하고 있어요",
    "abstain_insufficient_evidence": "근거가 부족해 안내 범위를 정리하고 있어요",
}


def step_message(node_name: str) -> str:
    """화면에 보여줄 진행 문구. 표에 없는 노드는 일반 문구로 덮는다."""

    return _STEP_MESSAGES.get(node_name, "요청을 처리하고 있어요")


@dataclass
class _Record:
    owner: object | None
    total_steps: int
    started_at: float
    updated_at: float
    message: str = _STARTING_MESSAGE
    status: str = RUNNING
    # 끝난 단계 "수"만 센다(어느 노드였는지는 화면에 내지 않는다). 되묻기
    # 재개는 이전 턴에서 끝낸 수를 물려받아 이어서 올라간다 - start() 참고.
    completed: int = 0
    # 이 요청이 시작할 때 이미 차 있던 비율. 재개 요청은 분모(total_steps)가
    # 이전 턴보다 커지기 때문에, 같은 완료 수여도 비율이 살짝 낮아진다 -
    # 막대가 한 칸 뒤로 가는 것처럼 보이지 않게 바닥값으로 쓴다.
    floor_fraction: float = 0.0
    finished_at: float | None = None


class ProgressRegistry:
    """요청 키 -> 진행 상황. 프로세스 전역 싱글턴(``PROGRESS``)으로 쓴다."""

    def __init__(self) -> None:
        self._records: OrderedDict[str, _Record] = OrderedDict()
        self._aliases: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- 쓰기 --------------------------------------------------------------

    def start(
        self,
        key: str,
        *,
        total_steps: int,
        owner: object | None = None,
        aliases: Iterable[str] = (),
        carried_steps: int = 0,
        carried_seconds: float = 0.0,
    ) -> None:
        """요청 하나의 추적을 시작한다(같은 키의 이전 기록은 덮어쓴다).

        ``aliases``는 클라이언트가 만들어 보낸 조회용 토큰이다 - 첫 상담
        요청(API-10)에서는 ``session_id``를 서버가 만들기 때문에 클라이언트가
        아직 키를 모르고, 그래서 진행률을 조회할 이름이 따로 필요하다.

        ``carried_steps``는 "이전 턴에서 이미 끝낸 단계 수"다. 되묻기에 답해
        재개하는 요청은 그래프를 처음부터 다시 돌지 않고 멈춘 자리에서
        이어가는데(builder.py E18b: N10a -> N9), 그때 진행률을 0부터 다시
        그리면 사용자에게는 "답했더니 처음부터 다시 함"으로 보인다. 이미
        끝낸 만큼을 물려받아 막대가 뒤로 가지 않게 한다. ``carried_seconds``는
        같은 상담의 이전 처리 시간으로, 사용자 답변을 기다린 시간은 제외한다.
        """

        now = time.monotonic()
        carried = max(int(carried_steps), 0)
        with self._lock:
            self._purge(now)
            previous = self._records.get(key)
            floor = (
                min(previous.completed / previous.total_steps, _RUNNING_CEILING)
                if carried and previous is not None
                else 0.0
            )
            self._records[key] = _Record(
                owner=owner,
                total_steps=max(int(total_steps), 1),
                started_at=now - max(carried_seconds, 0.0),
                updated_at=now,
                completed=carried,
                floor_fraction=floor,
            )
            self._records.move_to_end(key)
            for alias in aliases:
                if alias:
                    self._aliases[alias] = key

    def node_started(self, key: str | None, node_name: str) -> None:
        record = self._get(key)
        if record is None:
            return
        with self._lock:
            if record.status != RUNNING:
                return
            record.message = step_message(node_name)
            record.updated_at = time.monotonic()

    def node_finished(self, key: str | None, node_name: str) -> None:
        record = self._get(key)
        if record is None:
            return
        with self._lock:
            if record.status != RUNNING:
                return
            record.completed += 1
            record.updated_at = time.monotonic()

    def stage(self, key: str | None, message: str) -> None:
        """노드 실행이 아닌 구간(워밍업 대기 등)의 문구를 바꾼다."""

        record = self._get(key)
        if record is None:
            return
        with self._lock:
            if record.status != RUNNING:
                return
            record.message = message
            record.updated_at = time.monotonic()

    def finish(self, key: str, *, failed: bool = False) -> None:
        record = self._get(key)
        if record is None:
            return
        now = time.monotonic()
        with self._lock:
            record.status = FAILED if failed else DONE
            record.message = "요청을 처리하지 못했어요" if failed else "완료"
            record.updated_at = now
            record.finished_at = now

    def discard(self, key: str) -> None:
        with self._lock:
            self._records.pop(key, None)
            for alias, target in list(self._aliases.items()):
                if target == key:
                    del self._aliases[alias]

    # -- 읽기 --------------------------------------------------------------

    def completed_steps(self, key: str) -> int:
        """이 키가 지금까지 끝낸 단계 수. 기록이 없으면 0.

        되묻기 재개 요청이 ``start(carried_steps=...)``에 넘길 값을 읽어 가는
        용도다(진행 막대를 이어 그리기 위함).
        """

        record = self._get(key)
        return record.completed if record is not None else 0

    def snapshot(self, key_or_alias: str, *, owner: object | None = None) -> dict | None:
        """진행 상황 한 건. 소유자가 다르면 ``None``(= 그냥 '없음')."""

        record = self._get(key_or_alias)
        if record is None:
            return None
        if owner is not None and record.owner != owner:
            return None
        with self._lock:
            completed = record.completed
            if record.status == RUNNING:
                # 분모가 어림값이라 100%에 먼저 닿을 수 있다 - 끝나기 전에는
                # 95%에서 멈춰 "다 찼는데 안 끝나는" 막대를 만들지 않는다.
                # floor_fraction은 재개 요청이 뒤로 물러나지 않게 잡아준다.
                fraction = max(
                    min(completed / record.total_steps, _RUNNING_CEILING),
                    record.floor_fraction,
                )
            else:
                fraction = 1.0
            elapsed = (record.finished_at or time.monotonic()) - record.started_at
            return {
                "status": record.status,
                "fraction": round(fraction, 4),
                "message": record.message,
                "completed_steps": completed,
                "total_steps": record.total_steps,
                "elapsed_seconds": round(elapsed, 1),
            }

    # -- 내부 --------------------------------------------------------------

    def _get(self, key: str | None) -> _Record | None:
        if not key:
            return None
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                return record
            target = self._aliases.get(key)
            return self._records.get(target) if target else None

    def _purge(self, now: float) -> None:
        """호출자가 ``self._lock``을 쥔 상태에서만 부른다."""

        expired = {
            key
            for key, record in self._records.items()
            if record.finished_at is not None and now - record.finished_at > _RETENTION_SECONDS
        }
        for key in list(self._records):
            if len(self._records) - len(expired) <= _MAX_RECORDS:
                break
            # 오래된 것부터(OrderedDict 삽입 순) 버린다. 아직 도는 요청까지
            # 버릴 수 있지만, 그때는 진행률만 사라지고 응답 자체는 정상이다.
            expired.add(key)
        for key in expired:
            self._records.pop(key, None)
        for alias, target in list(self._aliases.items()):
            if target not in self._records:
                del self._aliases[alias]


PROGRESS = ProgressRegistry()

__all__ = ["PROGRESS", "ProgressRegistry", "step_message", "RUNNING", "DONE", "FAILED"]

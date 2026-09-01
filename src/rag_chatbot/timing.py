"""어디서 시간이 드는지 실제로 재는 계측.

왜 필요한가: "처음 실행이 오래 걸린다"는 걸 추측으로 진단하면 엉뚱한 데를
고치게 된다. 후보가 여럿이다 - vectorDB 인덱스 로딩(지원제도 청크 45,413개),
LLM 호출(대화 한 번에 11~12번), 추론형 모델의 내부 사고 토큰, 노드별 재검색.
어느 쪽이 지배적인지는 재봐야 안다.

쓰는 법::

    from ..timing import TIMER

    with TIMER.measure("vectordb_connect"):
        store = connect_store()

    TIMER.summary()   # [{"name", "count", "total_s", "avg_s", "share"}...]

측정 자체는 ``perf_counter`` 두 번 호출이라 사실상 공짜다. 그래서 플래그로
켜고 끄지 않고 항상 켜둔다 - 켜는 걸 잊어서 못 재는 쪽이 더 손해다.

한계(숨기지 않음): 모듈 전역 인스턴스 하나를 공유하므로, Streamlit처럼 여러
요청이 같은 프로세스에서 동시에 돌면 기록이 섞인다. 요청 단위로 보려면
``reset()`` 후 그 요청만 돌려야 한다(``RecordingLLMClient``와 같은 한계).
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager


class PhaseTimer:
    """이름표가 붙은 구간의 경과 시간을 누적한다."""

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        # 노드가 실제로 돈 순서. 그래프가 조건부 분기를 타기 때문에
        # "어떤 경로로 갔는지"는 실행해봐야만 알 수 있다.
        self._trace: list[tuple[str, float]] = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        self._totals.clear()
        self._counts.clear()
        self._trace.clear()

    def trace(self, node_name: str, seconds: float) -> None:
        self._trace.append((node_name, seconds))

    def path(self) -> list[tuple[str, float]]:
        """노드가 돈 순서와 각각의 소요 시간."""

        return list(self._trace)

    def record(self, name: str, seconds: float) -> None:
        # N5가 청크별 LLM 호출을 동시에 돌리므로 여러 스레드가 여기로 온다.
        with self._lock:
            self._totals[name] = self._totals.get(name, 0.0) + seconds
            self._counts[name] = self._counts.get(name, 0) + 1

    @contextmanager
    def measure(self, name: str):
        """구간을 잰다. 예외가 나도 기록하고 그대로 흘려보낸다.

        try/finally인 이유: LangGraph의 ``interrupt()``는 예외로 흐름을
        멈추는데, 그것까지 삼키면 되묻기가 동작하지 않는다. 시간만 남기고
        예외는 그대로 올려보낸다.
        """

        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - started)

    def summary(self) -> list[dict]:
        """오래 걸린 순으로 정렬한 요약.

        ``share``는 측정된 구간 합계 대비 비율이다. 구간끼리 겹치는 경우가
        있어(노드 시간 안에 LLM 호출 시간이 포함된다) 전체 실행 시간과는
        다르다 - 순위를 보는 용도지 합이 100%가 되는 값이 아니다.
        """

        total = sum(self._totals.values()) or 1.0
        rows = [
            {
                "name": name,
                "count": self._counts[name],
                "total_s": seconds,
                "avg_s": seconds / self._counts[name],
                "share": seconds / total,
            }
            for name, seconds in self._totals.items()
        ]
        return sorted(rows, key=lambda row: row["total_s"], reverse=True)


# 프로세스 전역 인스턴스. 노드/서비스/LLM 클라이언트가 같은 것을 쓴다.
TIMER = PhaseTimer()


# 설계 문서(xlsx "노드_Agent" 시트)의 노드 번호. 로그에 N번호를 함께 찍어야
# 코드와 설계표를 오가며 읽을 수 있다. 번호는 각 노드 파일의 docstring 첫
# 줄에 적힌 것을 그대로 따랐다.
NODE_NUMBERS = {
    "slot_parser": "N1",
    "slot_completeness_gate": "N2",
    "general_law_reference_search": "N2a",
    "request_missing_slots": "N3",
    "policy_search": "N4",
    "claim_plan": "N5",
    "document_verification": "N6",
    "evidence_gate": "N7",
    "targeted_law_search": "N8",
    "eligibility_verdict": "N9",
    "benefit_calculator": "N10",
    "duplicate_benefit": "N11",
    "result_assembly": "N12",
    "answer_generation": "N13",
    "final_verification": "N14",
    # 근거 부족으로 답변을 포기하는 경로. 설계표에 번호가 없다.
    "abstain_insufficient_evidence": "-",
}

# 노드가 무슨 일을 하는지 한 줄 설명. 실행 순서를 눈으로 따라갈 때
# 이름만으로는 감이 안 와서 함께 찍는다.
NODE_LABELS = {
    "slot_parser": "사용자 발화에서 슬롯 추출",
    "slot_completeness_gate": "하드 게이트 슬롯이 다 찼는지 판정",
    "general_law_reference_search": "지역 무관 참고 법령 검색",
    "request_missing_slots": "부족한 항목 되묻기(중단)",
    "policy_search": "지원제도 후보 검색",
    "claim_plan": "정책 원문에서 claim 후보 추출",
    "document_verification": "근거가 원문에 실제로 있는지 검증",
    "evidence_gate": "근거 충분한지 게이트",
    "targeted_law_search": "선언된 법령 메타데이터 정조준 검색",
    "eligibility_verdict": "자격 충족/미충족/미확인 판정",
    "benefit_calculator": "지원금액 계산",
    "duplicate_benefit": "중복수급 판정",
    "result_assembly": "정책별 결과 조립",
    "answer_generation": "최종 답변 문장 생성",
    "final_verification": "답변 최종 검증",
    "abstain_insufficient_evidence": "근거 부족으로 답변 보류",
}


def node_title(name: str) -> str:
    """``N9 eligibility_verdict - 자격 충족/미충족/미확인 판정`` 형태."""

    number = NODE_NUMBERS.get(name, "?")
    label = NODE_LABELS.get(name)
    return f"{number} {name}" + (f" - {label}" if label else "")


def timed_node(name: str, func):
    """LangGraph 노드를 감싸 실행 시간과 실행 순서를 기록한다.

    ``functools.partial``로 이미 감싼 노드도 그대로 받을 수 있게 이름을
    인자로 받는다(partial에는 ``__name__``이 없다).

    ``BOKJI_TRACE=1``이면 노드가 도는 즉시 한 줄씩 찍는다. 어디서 멈춰
    있는지 실시간으로 보려는 용도다(느린 노드를 기다리는 동안 화면이
    조용하면 멈춘 건지 도는 건지 알 수 없다).
    """

    def _wrapped(*args, **kwargs):
        started = time.perf_counter()
        if os.environ.get("BOKJI_TRACE") == "1":
            print(f"  -> {node_title(name)} ...", flush=True)
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            TIMER.record(f"node:{name}", elapsed)
            TIMER.trace(name, elapsed)
            if os.environ.get("BOKJI_TRACE") == "1":
                print(f"     {node_title(name)} 완료 ({elapsed:.2f}초)", flush=True)

    return _wrapped


__all__ = [
    "TIMER",
    "PhaseTimer",
    "timed_node",
    "node_title",
    "NODE_NUMBERS",
    "NODE_LABELS",
]

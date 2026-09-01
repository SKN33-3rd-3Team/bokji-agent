"""N1~N14 전체 파이프라인을 프론트엔드 없이 콘솔에서 직접 대화하며 확인하는
수동 테스트 스크립트 (Issue #25 graph builder + N1~N3 통합 확인용).

사용법 (레포 루트에서, PowerShell 기준):
    pip install -r requirements-graph.txt
    $env:PYTHONPATH = ".;src"
    python scripts/interactive_console_chat.py

무엇을 하는가
-------------
- 실제 서비스 vectorDB(``data/vector_db``, collection_prefix
  ``bokji_rag``)에 그대로 연결한다. 이 DB는 ``HashEmbeddingProvider(128)``
  (Chroma 컬렉션 메타데이터의 ``rag_embedding_provider: "local-hash-v1:128"``
  로 확인함)로 색인돼 있어서 이 스크립트도 같은 provider로 연결해야 한다 -
  다른 차원/provider로 열면 ``VectorStoreError``/차원 불일치가 난다. 이
  스크립트는 이 DB에 쓰기(색인)는 전혀 하지 않고 검색만 한다. (vectorDB
  연결/LLM 클라이언트 구성 자체는 ``src/rag_chatbot/service.py``로 옮겼다 -
  이 스크립트는 그걸 그대로 가져다 쓴다. Streamlit 등 다른 프론트엔드도
  같은 함수를 쓰므로, 연결 방식이 바뀌면 한 곳만 고치면 된다.)
- ``build_graph()``로 N1~N14 전체를 조립하고, 콘솔에 입력한 문장을
  ``graph.stream(..., stream_mode="updates")``로 흘려보낸다. ``invoke()``
  대신 ``stream()``을 쓰는 이유가 이 스크립트의 핵심이다 - 최종 결과만
  받으면 N4~N14가 한 번에 통째로 실행돼서 그 사이에 무슨 일이 있었는지 안
  보이는데, ``stream()``은 노드가 하나 끝날 때마다 그 노드 이름과 반환값을
  바로 넘겨주므로 **어떤 노드를 지나는지 노드별로 구분해서** 콘솔에 그대로
  찍을 수 있다. 노드를 하나 지날 때마다

        ----------------------------
        N번호
        ----------------------------
         <그 시점까지 누적된 state>

  형식으로 출력하고, 그래프가 끝까지 실행되면 최종 결과를 ``<최종출력>``
  아래에 따로 보여준다.
- N3(추가 정보 요청)가 ``interrupt()``로 그래프를 실제로 멈추면 그 질문을
  콘솔에 그대로 보여주고, 다음 입력을 ``Command(resume=...)``로 이어 붙인다
  - 실제 서비스에서 프론트엔드가 할 일을 여기서는 ``input()``이 대신한다.

한 세션(대화) 안에서는 슬롯이 다 채워질 때까지(최대 MAX_SLOT_ASKS=2회씩)
N1↔N2↔N2a/N3 루프만 반복된다 - N4 이후로 넘어가면 같은 stream() 호출
안에서 끝까지(N14) 실행되고 더 이상 멈추지 않는다(다이어그램에 N4 이후
루프백 간선이 없음). 그래서 이 스크립트는 "대화 하나 = session_id 하나"로
다루고, 답변이 나오면 새 대화를 시작할지 물어본다.

LLM 연결 (HF_TOKEN이 있으면 자동으로 붙는다)
---------------------------------------------
``build_graph()``에 실제로 LLM을 주입할 수 있는 자리는 N5(claim_plan)·
N9(eligibility_verdict)·N10(benefit_calculator)·N13(answer_generation) 네
곳이다(각각 claim 추출/위반 사유 자연어화/금액 추출/답변 문장 다듬기).
N1(slot_parser)·N7(evidence_gate)은 애초에 LLM을 주입할 자리 자체가 없다
(함수 시그니처에 llm_client 인자가 없음 - N1이 속한 ``llm_gateway`` 모듈
이름과 달리, 지금 구현은 "실제 자연어 이해가 아니라 규칙 기반
placeholder"라고 그 파일 docstring에 명시돼 있다).

이 스크립트는 환경변수 ``HF_TOKEN``이 있으면 ``src/rag_chatbot/service.py``의
``build_llm_client()``가 자동으로 ``HuggingFaceInferenceClient``
(``src/rag_chatbot/llm/client.py``)를 만들어 저 네 노드에 실제로 붙인다.
토큰이 없으면(기본 상태) 지금까지처럼 넷 다 규칙 기반/템플릿 경로로만
동작한다 - LLM 없이도 항상 끝까지 도는 성질은 그대로 유지된다.

``HF_TOKEN``/``LLM_MODEL_NAME``은 실행할 때마다 매번 셸에 치는 대신
``.env`` 파일에 넣어두면 된다 - 이 스크립트가 시작할 때
``python-dotenv``로 자동으로 읽는다(``.env``는 ``.gitignore``에 이미
있으므로 커밋되지 않는다). ``.env.example``에 항목을 추가해뒀다.

    # .env (레포 루트, .env.example 참고)
    HF_TOKEN=hf_...
    LLM_MODEL_NAME=Bllossom/llama-3.2-Korean-Bllossom-3B   # 생략하면 이 기본값을 씀

(예전 이름 ``LLM_HF_MODEL``도 하위 호환으로 계속 읽는다 - ``LLM_MODEL_NAME``이
없을 때만 그 값을 본다. 새로 설정할 땐 ``LLM_MODEL_NAME``을 쓰는 걸 권장한다.)

셸에서 그때그때 넘기고 싶으면(.env보다 우선) PowerShell에서
``$env:HF_TOKEN = "hf_..."``도 여전히 된다.

``python-dotenv``/``huggingface_hub``가 이 리포 기존 requirements에는
없었어서 ``requirements-graph.txt``에 추가해뒀다 -
``pip install -r requirements-graph.txt``면 같이 설치된다. 후보 모델이
HuggingFace의 무료 서버리스 Inference API에 "warm"하게 떠 있지 않으면
호출이 실패할 수 있다(``HuggingFaceInferenceClient`` docstring 참고) - 그
경우 ``scripts/llm_prompt_probe.py``로 어느 모델이 되는지 먼저 확인하는
걸 권장한다.

그 외 한계
----------
- 검색은 해시 기반 임베딩이라 진짜 의미 검색만큼 관련성이 좋지 않을 수
  있다. 다만 실제 색인된 정책 4만5천여 건을 그대로 검색하므로, 지역/관심사
  키워드가 맞으면 실제로 존재하는 제도가 잡힌다.
- 대화 상태(``MemorySaver``)는 이 프로세스가 떠 있는 동안에만 유지된다.
  스크립트를 다시 실행하면 이전 세션은 사라진다.

입력 예시 (규칙 기반 슬롯 추출기가 알아듣는 표현들)
----------------------------------------------------
- 지역: "서울특별시 종로구", 그냥 "서울"도 가능
- 생년월일: "1990년 5월 12일생" 또는 "1990-05-12"
- 성별: "여성이에요" / "남성입니다"
- 소득: "기초생활수급자입니다" 등
- 장애 여부: "장애가 있어요" / "장애는 없고"
- 취업 상태: "회사 다니고 있어요" / "취업 준비 중이에요"

대화 중 명령어
--------------
    /slots        지금까지 모인 slots를 그대로 출력 (N3 질문에 답하는 중에도 가능)
    /quit, /exit  종료
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from src.rag_chatbot.graph import build_graph
from src.rag_chatbot.service import build_llm_client, connect_store

# 그래프 조립 단계(builder.py)의 노드 이름 -> 다이어그램 노드 번호/설명.
# 콘솔에 "지금 어느 노드를 지나는지"를 사람이 읽을 수 있게 붙이는 용도일
# 뿐이라, 이 표를 새로 만들지 않고 builder.py를 import해서 재사용하지는
# 않는다(그래프 내부 구현에 의존하지 않게 하려는 의도).
_NODE_LABELS = {
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
    "abstain_insufficient_evidence": "N7-fail (확인 불가 종착)",
}

# 콘솔 안내 메시지용 표시 값. 실제 연결 경로/설정은 service.connect_store()가
# 갖고 있다 - 여기서는 로그에 찍을 문자열로만 쓴다(연결 로직 자체를
# 두 곳에 중복시키지 않기 위해).
_REAL_VECTOR_DB_PATH = Path("data/vector_db")

_QUIT_COMMANDS = {"/quit", "/exit"}
_DIVIDER = "-" * 28
_FINAL_OUTPUT_MARKER = "<최종출력>"


def _summarize_value(value: object) -> str:
    """state 필드 하나를 콘솔에 보기 좋게 줄인다.

    문자열은 그대로 보여주고(draft_answer 같은 서술형 값이 json.dumps
    따옴표로 어색해지지 않게), RetrievedChunk 리스트(subsidy_chunks/
    law_chunks)는 본문 전체 대신 doc_id만 요약한다 - 안 그러면 청크 본문이
    통째로 찍혀서 어느 노드가 무슨 일을 했는지 오히려 안 보인다. 나머지는
    JSON으로 찍되 너무 길면 잘라낸다.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and hasattr(value[0], "chunk"):
        doc_ids = [item.chunk.doc_id for item in value]
        return f"{len(value)}건 - {doc_ids}"
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 1000:
        text = text[:1000] + "\n  ...(생략)"
    return text


def _format_state(state: dict) -> str:
    lines: list[str] = []
    for key, value in state.items():
        lines.append(f" {key}:")
        for line in _summarize_value(value).splitlines() or [""]:
            lines.append(f"   {line}")
    return "\n".join(lines)


def _print_node_block(node_name: str, state: dict) -> None:
    """노드를 하나 지날 때마다 요청받은 형식대로 그 시점 state를 찍는다.

    ----------------------------
    N번호
    ----------------------------
     <state>
    """

    label = _NODE_LABELS.get(node_name, node_name)
    print(f"\n{_DIVIDER}\n{label}\n{_DIVIDER}")
    print(_format_state(state))


def _print_final_output(state: dict) -> None:
    print(f"\n{_FINAL_OUTPUT_MARKER}")
    print(f"[answer_status] {state.get('answer_status')}")
    print(state.get("final_answer") or "(final_answer 없음)")

    citations = state.get("final_citations") or []
    if citations:
        print("\n[근거]")
        for citation in citations:
            print(f"- {citation.get('label')}: {citation.get('source_url')}")
    else:
        print("\n[근거] 없음")

    print("\n[수집된 slots]")
    print(json.dumps(state.get("slots", {}), ensure_ascii=False, indent=2, default=str))
    print(f"\n[node_trace] {state.get('node_trace')}")


def _print_slots(state: dict) -> None:
    print(json.dumps(state.get("slots", {}), ensure_ascii=False, indent=2, default=str))


def _read_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        raise SystemExit(0)


def _run_conversation(graph, session_id: str) -> None:
    """세션 하나(대화 하나)를 시작부터 끝까지(N1~N14, 필요하면 N3 재입력
    루프까지) 진행하면서, 노드가 하나 끝날 때마다 그 결과를 바로 찍는다.

    ``graph.invoke()`` 대신 ``graph.stream(..., stream_mode="updates")``를
    쓴다 - invoke는 그래프가 멈추거나(인터럽트) END에 닿을 때까지의 최종
    결과만 한 번에 돌려주지만, stream은 노드 하나가 끝날 때마다
    ``{노드_이름: 그_노드가_반환한_dict}``를 즉시 넘겨준다. N3에서 실제로
    interrupt가 걸리는 시점에는 그 노드 자체가 아직 안 끝난 상태라
    ``{"__interrupt__": (Interrupt(...),)}``가 대신 온다(request_missing_slots
    자체의 반환값은 재개된 뒤에야 다음 stream 호출에서 나온다).
    """

    print(f"\n[새 대화 시작] session_id={session_id}")
    user_input = _read_line("나: ")
    if user_input in _QUIT_COMMANDS:
        raise SystemExit(0)

    state: dict = {
        "query_id": session_id,
        "as_of": date.today(),
        "user_input": user_input,
        "slots": {},
        "slot_ask_counts": {},
        "node_trace": [],
        "safety_blocked": False,
    }
    config = {"configurable": {"thread_id": session_id}}
    stream_input: object = state

    while True:
        interrupt_question: str | None = None

        for chunk in graph.stream(stream_input, config=config, stream_mode="updates"):
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    interrupt_question = update[0].value
                    continue
                state.update(update)
                _print_node_block(node_name, state)

        if interrupt_question is None:
            break  # 더 이상 멈추지 않고 END까지 도달함

        print(f"\n{_DIVIDER}\nN3 - 사용자 입력 대기\n{_DIVIDER}")
        print(interrupt_question)
        print(f"(참고 - 지금까지 부족한 슬롯: {state.get('missing_slots')})")

        while True:
            reply = _read_line("> ")
            if reply == "/slots":
                _print_slots(state)
                continue
            if reply in _QUIT_COMMANDS:
                raise SystemExit(0)
            break

        stream_input = Command(resume=reply)

    _print_final_output(state)


def main() -> None:
    print("정책 상담 콘솔 테스트 - 프론트엔드 없이 N1~N14 파이프라인과 직접 대화합니다.")
    print(f"실제 서비스 vectorDB({_REAL_VECTOR_DB_PATH})에 연결합니다.")
    print("아무 때나 /slots(현재 슬롯 보기), /quit(종료) 입력 가능.\n")

    store = connect_store()
    llm_client = build_llm_client()
    if llm_client is None:
        print(
            "[LLM] HF_TOKEN이 없어 N1/N5/N9/N10/N13는 규칙 기반/템플릿 경로로만 동작합니다. "
            "실제 LLM을 붙이려면 HF_TOKEN(과 필요하면 LLM_MODEL_NAME) 환경변수를 설정하세요."
        )
    else:
        print(f"[LLM] HF_TOKEN 감지됨 - {llm_client.model} 로 N1/N5/N9/N10/N13에 실제 LLM을 붙입니다.")
    graph = build_graph(store, llm_client=llm_client)

    while True:
        _run_conversation(graph, session_id=f"console-{uuid.uuid4().hex[:8]}")
        again = _read_line("\n다른 질문을 새 대화로 시작할까요? (Enter=예, /quit=종료) ")
        if again in _QUIT_COMMANDS:
            print("종료합니다.")
            return


if __name__ == "__main__":
    main()

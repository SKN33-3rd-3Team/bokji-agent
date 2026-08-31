"""src/rag_chatbot/service.py(ask/answer_followup)가 실제로 Streamlit에 뭘
돌려주는지 콘솔에서 그대로 확인하는 수동 테스트 스크립트.

scripts/interactive_console_chat.py와 다른 점: 그 스크립트는 그래프를 직접
``graph.stream()``으로 돌려서 N1~N14 노드별 진행 과정을 보여주는 "그래프
디버깅용"이고, 이 스크립트는 Streamlit이 실제로 호출할 ``service.ask()``/
``service.answer_followup()``만 그대로 호출해서 **그 반환값(ChatResponse/
PolicyView)이 화면에 뭘 채울 수 있는지**를 확인하는 "프론트엔드 연동
확인용"이다. 노드 진행 과정은 안 보여준다.

사용법 (레포 루트에서, PowerShell 기준):
    pip install -r requirements-graph.txt
    $env:PYTHONPATH = ".;src"
    python scripts/manual_test_service.py

대화 중 명령어
--------------
    /quit, /exit  종료
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag_chatbot.service import ask, answer_followup

_QUIT_COMMANDS = {"/quit", "/exit"}
_DIVIDER = "=" * 60


def _read_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        raise SystemExit(0)


def _print_policy(policy: dict) -> None:
    print(
        f"\n[{policy['rank']}] {policy['title']}  ({policy['badge']})"
        f"  - 자격: {policy['eligibility_status']}  금액: {policy['amount_label']}"
    )
    if policy["eligibility_reasons"]:
        print(f"    근거: {' / '.join(policy['eligibility_reasons'])}")
    print(f"    중복수급: {policy['duplicate_status']}", end="")
    if policy["duplicate_note"]:
        print(f" ({policy['duplicate_note']})", end="")
    print()
    if policy["needs_confirmation"]:
        print(f"    [확인 필요] {' / '.join(policy['needs_confirmation'])}")
    detail = policy["detail"]
    print(f"    지역: {detail.get('region_names')} | 나이: {detail.get('age_start')}~{detail.get('age_end')}")
    print(f"    기관: {detail.get('organization')} | 출처: {detail.get('source_url')}")
    for key, label in [
        ("purpose", "목적"), ("support_target", "지원대상"),
        ("eligibility_criteria", "선정기준"), ("support_details", "지원내용"),
        ("application_method", "신청방법"), ("application_period", "신청기한"),
        ("legal_basis", "근거법령"),
    ]:
        value = detail.get(key)
        if value:
            preview = value if len(value) <= 150 else value[:150] + "...(생략)"
            print(f"    [{label}] {preview}")


def _print_llm_status(response: dict) -> None:
    """LLM이 실제로 돌았는지 화면에 드러낸다.

    노드들은 LLM 호출이 실패해도 규칙 기반으로 폴백하기 때문에, 이걸 안
    찍으면 "LLM이 한 번도 안 돌았는데 결과는 멀쩡히 나오는" 상태를 알 수
    없다(실제로 HF 토큰이 403인 동안에도 답변은 정상으로 보였다).
    """

    status = response.get("llm_status")
    if not status:
        return
    if not status.get("enabled"):
        print(f"\n[LLM] 미사용 - {' / '.join(status.get('messages') or ['HF_TOKEN 없음'])}")
        return

    calls = status.get("calls")
    successes = status.get("successes")
    failures = status.get("failures")
    model = status.get("model")
    if failures:
        print(f"\n[LLM] !! 실패 - 모델={model} / 호출 {calls}회 중 성공 {successes}회, 실패 {failures}건")
        print("      아래 정책 판정/문구는 LLM 없이 규칙 기반으로만 만들어진 것입니다.")
        for message in status.get("messages") or []:
            print(f"      - {message}")
        print("      원인을 자세히 보려면: python scripts/check_llm_connection.py")
    elif calls:
        print(f"\n[LLM] 정상 - 모델={model} / 호출 {calls}회 전부 성공")
    else:
        print(f"\n[LLM] 모델={model} 준비됨(이번 턴에는 호출 없음)")


def _print_response(response: dict) -> None:
    print(f"\n{_DIVIDER}")
    print("[ChatResponse 원본(JSON, detail 생략)]")
    slim = {k: v for k, v in response.items() if k not in ("policies", "llm_status")}
    if "policies" in response:
        slim["policies"] = [{k: v for k, v in p.items() if k != "detail"} for p in response["policies"]]
    print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))

    _print_llm_status(response)

    if response["status"] == "needs_input":
        print(f"\n[N3 질문] {response['question']}")
        print(f"[부족한 슬롯] {response['missing_slots']}")
        return

    print(f"\n[answer_status] {response['answer_status']}")
    print(f"[final_answer]\n{response['final_answer']}")
    print(f"\n[정책 카드 {len(response['policies'])}건]")
    for policy in response["policies"]:
        _print_policy(policy)


def main() -> None:
    print("service.ask()/answer_followup()이 Streamlit에 실제로 돌려줄 값을 그대로 확인합니다.")
    print("아무 때나 /quit(종료) 입력 가능.\n")

    while True:
        session_id = f"svc-console-{uuid.uuid4().hex[:8]}"
        print(f"\n[새 대화 시작] session_id={session_id}")
        user_input = _read_line("나: ")
        if user_input in _QUIT_COMMANDS:
            return

        response = ask(user_input, session_id)
        _print_response(response)

        while response["status"] == "needs_input":
            reply = _read_line("\n> ")
            if reply in _QUIT_COMMANDS:
                return
            response = answer_followup(session_id, reply)
            _print_response(response)

        again = _read_line("\n다른 질문을 새 대화로 시작할까요? (Enter=예, /quit=종료) ")
        if again in _QUIT_COMMANDS:
            print("종료합니다.")
            return


if __name__ == "__main__":
    main()

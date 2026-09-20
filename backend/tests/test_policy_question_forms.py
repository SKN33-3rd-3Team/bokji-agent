"""문장 형식과 무관한 정책 문의 및 기존 근거 검증 회귀."""
import json

import pytest

from backend.app.services import followup_adapter
from backend.app.session_store.chat_session import chat_session_store
from src.rag_chatbot import light_followup


class Answers:
    def __init__(self, *, quote="주민센터 방문 신청", consistent=True, answerable=True):
        self.calls = []
        self.responses = [json.dumps({
            "answerable": answerable, "answer": "주민센터에 방문해 신청합니다.",
            "evidence_quotes": [quote],
        }, ensure_ascii=False), json.dumps({"consistent": consistent})]

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


@pytest.mark.parametrize("question", [
    "신청방법", "신청방법 알려줘", "신청방법을 설명해 주세요", "신청방법이 궁금해",
    "어떻게 신청하나요", "어떻게 신청하나요?",
    "정책 상세 알려줘",
])
def test_policy_question_forms_reach_generation_and_verification(client, monkeypatch, question):
    signup = client.post("/api/v1/auth/signup", json={
        "email": "forms@example.com", "name": "문의 검사", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    })
    assert signup.status_code == 201
    user_id = client.get("/api/v1/users/me").json()["id"]
    chat_session_store.create("forms-session", user_id=user_id)
    chat_session_store.update_last_response("forms-session", policies=[{
        "policy_id": "P1", "title": "지원 제도", "detail": {"application_method": "주민센터 방문 신청"},
    }], profile=[])
    llm = Answers()
    monkeypatch.setattr(followup_adapter, "get_llm_client", lambda: llm)
    response = client.post("/api/v1/chat/sessions/forms-session/policies/P1/questions",
                           json={"question": question})
    assert response.status_code == 200
    assert response.json()["kind"] == "answer"
    assert response.json()["evidence_quotes"] == ["주민센터 방문 신청"]
    assert len(llm.calls) == 2  # 생성 뒤에도 근거 일치 검증을 반드시 수행한다.
    assert llm.calls[0][0].endswith(question)
    assert "물음표나 의문형 어미의 유무" in llm.calls[0][1]["system"]


@pytest.mark.parametrize("case,reason", [
    ("unrelated", "not_answerable"), ("fabricated", "quote_not_found"),
    ("inconsistent", "inconsistent"),
])
def test_non_question_requests_do_not_bypass_evidence_checks(monkeypatch, case, reason):
    monkeypatch.setattr(light_followup, "_UNVERIFIED_RETRIES", 0)
    llm = Answers(quote="없는 서류 제출" if case == "fabricated" else "주민센터 방문 신청",
                  consistent=case != "inconsistent", answerable=case != "unrelated")
    response = light_followup.respond_to_policy_question(
        {"title": "지원 제도", "detail": {"application_method": "주민센터 방문 신청"}},
        "다른 정책 알려줘" if case == "unrelated" else "신청방법 알려줘", llm_client=llm,
    )
    assert response["kind"] == "guidance"
    assert response["reason"] == reason


@pytest.mark.parametrize("question", ["자녀수별로 다른 지원금 알려줘", "정책 상세 알려줘"])
def test_first_meeting_voucher_requests_preserve_birth_order_evidence(question):
    # 공개 정책 원문을 재현하는 대신 검사용 금액을 사용한다.
    excerpt = "출생 순위에 따라 첫째 100원, 둘째 이상 200원을 지원한다."
    llm = Answers()
    llm.responses = [json.dumps({
        "answerable": True,
        "answer": "출생 순위 기준으로 첫째 100원, 둘째 이상 200원을 지원합니다.",
        "evidence_quotes": [excerpt],
    }, ensure_ascii=False), json.dumps({"consistent": True})]
    result = light_followup.respond_to_policy_question({
        "title": "첫만남 이용권", "detail": {"support_details": excerpt},
    }, question, llm_client=llm)
    assert result["kind"] == "answer"
    assert result["evidence_quotes"] == [excerpt]
    assert "가구의 총 자녀 수와 출생 순위" in llm.calls[0][0]
    assert question in llm.calls[0][0]

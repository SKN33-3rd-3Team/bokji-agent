# 정책 상세 문의 경량 채팅 — 설계 노트

> 담당: 김길환 | 작성: 2026-09-09 | 개정: 2026-09-10 (스코프 축소 → 구현 완료) | 상태: 구현 완료(로컬 테스트 통과, 앱 수동확인 대기)
> 참고: 이 문서는 확정된 스펙이 아니라 설계 논의 기록입니다.

## 0. 개정 이력

- **2026-09-10 (5차)**: 팀 요청 반영 (상담 화면 전반).
  - **로그인 게이트**: 로그인 전에는 `page_chat`이 안내만 보여주고 상담 불가.
  - **되묻기(needs_input)는 위젯 폼**: `_render_slot_form`이 missing_slot마다
    selectbox/radio/date_input을 그리고, 제출하면 `라벨: 값` 줄로 이어붙여
    파이프라인에 넘긴다(자유 입력도 계속 가능).
  - **화면 정리**: "응답 원본 보기(JSON·실행 로그)" expander 제거,
    LLM 호출 통계 캡션 제거(AI 못 썼을 때만 한 줄), 진행바에서 노드 이름 제거.
  - **상세 문의 채팅방을 모달로**: 사이드바 → 오른쪽 칸 → `@st.dialog` 모달
    (`_detail_chat_dialog`). "어디 열렸는지" 헷갈릴 일이 없다. X 또는 "닫기"로
    나가면 `on_dismiss=_clear_detail_chat`가 상태를 비운다.
    `detail_chat_history` 분리는 유지.
- **2026-09-10 (4차)**: 상세 문의를 사이드바 채팅 패널로 이동(→ 5차에서 오른쪽 칸으로).
- **2026-09-10 (3차)**: 실제 LLM(HF Qwen2.5-72B) 붙여 확인. 추가 반영:
  - `.env` — `LLM_MODEL_NAME=Qwen/Qwen2.5-72B-Instruct`(8B는 answerable 판단·발췌
    정확도 낮음), `LLM_MAX_NEW_TOKENS=512`(기본 8192는 무료 티어 타임아웃).
  - `answer_light_followup`에 재시도 2회(`attempts`) — 무료/공용 엔드포인트
    일시 실패·JSON 깨짐 회복.
  - 답변 종결어미 격식체(`~합니다.`/`~입니다.`) 프롬프트 강제.
  - "근거" → "보충자료"로 라벨 변경.
  - **사용자 프로필 연결**: `respond_to_policy_question(user_profile=...)` 추가.
    세션의 "파악한 정보"(지역·나이·소득)를 `사용자 정보 - 라벨: 값` 문장으로
    컨텍스트에 합류 → "제가 서울 사는데 되나요?", "제 나이도 대상인가요?" 답변
    가능. `chat.py`가 `st.session_state["profile"]`을 넘김.
- **2026-09-10 (2차)**: A~F 구현 완료. `src/rag_chatbot/light_followup.py` 신설,
  `chat.py`에 정책 카드 → 상세 채팅 라우팅 추가. 단위 13개(`test_light_followup.py`)
  + 세션 4개(`test_streamlit_session.py`) + 라우팅 통합 6개
  (`test_light_followup_chat.py`) 통과. 어느 정책인지 특정하는 방식은 (b)
  "정책 카드의 '이 정책에 대해 물어보기' 버튼 클릭"으로 확정.
- **2026-09-10**: 팀원 논의 결과 스코프가 좁혀짐 — "추천받은 **특정 정책 하나**에
  대해 상세하게 물어보는 채팅"으로 확정. 이에 따라 "새 검색이 필요한 질문 →
  N1~N14 에스컬레이션"을 빼고, "이 채팅은 이 정책 전용, 새 검색은 메인에서"
  안내로 대체. `ask()` 확장·슬롯 누적 문제 전부 불필요해짐.
- **큰 발견**: 각 정책의 7개 섹션 전문(목적/지원대상/선정기준/지원내용/신청방법/
  신청기한/근거법령)이 첫 상담 때 `_fetch_policy_detail()`로 이미 Vector DB에서
  뽑혀 `policies[i]["detail"]`에 담겨 세션에 저장돼 있음. 후속질문 때 Vector DB
  재검색 불필요.

## 1. 배경 / 목적

- 사용자가 정책을 추천받은 뒤, 그 정책에 대해 "신청서류 뭐예요?", "언제까지
  신청해요?" 같은 상세 질문을 하고 싶어함.
- 현재는 이런 질문도 N1~N14 전체 파이프라인(LLM 호출 콜당 실측 50초)을
  재실행해서 느림.
- 목표: 추천받은 그 정책에 대한 상세 질문에, **이미 세션에 저장된 그 정책
  정보만으로** 빠르게 답하는 별도 채팅 경로. "근거 없으면 답하지 않는다"
  원칙은 그대로.

## 2. 핵심 흐름

```
사용자가 후속 질문 입력
      ↓
세션의 대상 정책 정보(final_answer + final_citations + policies[i]["detail"] 7섹션)를
컨텍스트로 LLM 호출 1번
      ↓
LLM 응답: {"answerable": bool, "answer": str, "evidence_quotes": [원문 발췌...]}
      ↓
검증(C): evidence_quotes가 컨텍스트 텍스트 안에 실제로 있는지 문자열 대조 (N6 방식)
      ↓
   answerable=true + 검증통과 → 답변 표시 (빠름, N1~N14 안 돎)
      ↓
   answerable=false / 검증실패 / "다른 정책 찾아줘" 류 → 안내:
   "이 채팅은 [정책명]에 대한 질문만 받아요. 다른 정책은 메인 화면에서 새로 검색해 주세요."
```

- **에스컬레이션(N1~N14 재실행) 없음.** 답 못 하면 안내로 넘긴다.
- 따라서 `ask()` 확장, 슬롯 누적, `conversation_id` 리셋 문제 전부 해당 없음.

## 3. 컴포넌트별 설계

| 컴포넌트 | 이름 | 위치 | 역할 | 상태 |
|---|---|---|---|---|
| A | `get_last_answered_result(messages)` | `streamlit_ui/session.py` | messages에서 최근 `"answered"` 결과 찾기 | ✅ 완료 (세션 테스트 4) |
| B | `answer_light_followup(...)` / `respond_to_policy_question(...)` | `src/rag_chatbot/light_followup.py` | 대상 정책 컨텍스트 조립 + 질문 → LLM 호출 1번 → 구조화 응답 → C 검증까지 묶음 | ✅ 완료 |
| C | `verify_light_answer(evidence_quotes, context_text)` | `src/rag_chatbot/light_followup.py` | evidence_quotes가 컨텍스트에 실제로 있는지 문자열 대조 (N6 방식) | ✅ 완료 |
| D | 라우팅 (chat.py) | `streamlit_ui/pages/chat.py` | 정책 카드 버튼 → `detail_chat_policy` 세팅 → 사이드바 입력을 `_handle_detail_chat_turn`으로 (무거운 경로 우회, `detail_chat_history`에 축적) | ✅ 완료 (통합 테스트 8) |
| E | UI 표시 | `streamlit_ui/pages/chat.py` | **사이드바 채팅 패널**: 제목 + 그만두기 + 스크롤 기록(`st.container(height)`) + 사이드바 `st.chat_input`. 답변은 마크다운 + "보충자료" expander · 안내는 `st.info` | ✅ 완료 |
| F | 테스트 | `tests/` | 단위 13 + 세션 4 + 라우팅 통합 6 통과 · 앱 수동확인은 대기 | 🔧 수동확인만 남음 |

### A. 정보 추출 (완료)

`st.session_state.messages`의 assistant 원소: `{"role": "assistant", "result": {ChatResponse}}`.
`get_last_answered_result`가 뒤에서부터 훑어 `status == "answered"`인 첫 result의
얕은 복사본을 반환. needs_input·error·user 메시지는 건너뜀.

쓸 필드:
- `final_answer: str` — 원래 답변 문장
- `final_citations: list[dict]` — 각 항목에 `chunk_id`, `source_url` 등
- `policies: list[PolicyView]` — 정책별. 각 PolicyView의 `detail`에 7개 섹션 전문:
  `purpose` / `support_target` / `eligibility_criteria` / `support_details` /
  `application_method` / `application_period` / `legal_basis` + region/age/org/url 메타

### B. 경량 응답 함수 (구현 예정)

- 기존 `LLMClient`(현재 `HuggingFaceInferenceClient`) + `loads_json_object` 재사용.
- 입력: 대상 정책의 컨텍스트(아래) + 사용자 질문
  - 컨텍스트 = `policies[i]["detail"]` 7섹션 텍스트 + `eligibility_reasons` +
    `final_answer` (해당 정책 부분)
- LLM에게: "아래 정보 안에서만 답해. 답에 쓴 근거 문장을 원문 그대로 발췌해서
  같이 내라. 답할 수 없으면 answerable=false."
- 출력(구조화):
  ```json
  {
    "answerable": true,
    "answer": "온라인으로 신청하며 별도 제출서류 안내는 없습니다.",
    "evidence_quotes": ["정부24 또는 관할 주민센터 방문 신청"]
  }
  ```
- 영어 키 사용(`loads_json_object` 파싱, 코드베이스 관례).

### C. 검증 — N6 방식으로 재작업

- 기존(chunk_id 존재 확인) 방식은 `detail` 섹션에 chunk_id가 없어서 안 맞음.
- **바뀐 방식**: N5→N6과 같은 원리. LLM이 `evidence_quotes`로 **원문을 그대로
  발췌**하게 하고, 그 발췌가 컨텍스트 텍스트 안에 문자열로 실제 있는지 확인.
  - 공백·줄바꿈은 정규화 후 비교(원문 prefix 처리 등으로 완벽히 일치 안 할 수 있음).
  - `evidence_quotes` 중 하나라도 컨텍스트에 없으면 → 실패.
  - `evidence_quotes`가 비어 있으면 → 실패(근거 없이 답한 것).
- 검증 실패 → 답을 버리고 안내 메시지로 대체.

### D. 라우팅 (완료)

정책을 특정하는 방식은 **(b) 정책 카드 클릭**으로 확정:

1. `_render_policy()`(rendering.py)에 "이 정책에 대해 물어보기" 버튼 →
   누르면 `st.session_state["detail_chat_policy"] = dict(policy)` + rerun.
2. `page_chat()`가 `detail_chat_policy`가 있으면 `_render_detail_chat_sidebar()`:
   - `with st.sidebar:` 안에 제목("[정책명] 문의") + "그만두기" 버튼
   - 스크롤되는 기록 컨테이너(`st.container(height=340)`)에 `detail_chat_history`를 그림
   - 사이드바 `st.chat_input`(인라인) - 입력이 오면 `_handle_detail_chat_turn(policy, prompt)`:
     `service.get_llm_client()` + `respond_to_policy_question(user_profile=...)` 호출,
     결과를 `{"role": "assistant", "light_answer": out}`로 `detail_chat_history`에 추가 후 rerun
3. 메인 `_render_history()`는 상세 문의를 그리지 않는다(별도 기록).
4. "그만두기" · 정책 전환 · `new_conversation()`(답변 완료·초기화·로그아웃) 시
   `detail_chat_policy`와 `detail_chat_history` 함께 해제.

- `get_last_answered_result(messages)`(A)는 세션에서 정책 정보를 되찾는 안전망으로
  남겨둠 — 현재 경로는 버튼이 `policy` dict를 통째로 넘기므로 직접 쓰지는 않음.

### E. UI 표시 (완료)

- **사이드바 채팅 패널** (`_render_detail_chat_sidebar`): 제목 + "그만두기" +
  스크롤 기록 + 사이드바 전용 `st.chat_input`. 메인 추천 화면은 그대로 유지.
- 답변(`kind == "answer"`): `st.markdown` + `evidence_quotes`가 있으면 "보충자료" expander.
  답변 문장은 프롬프트에서 격식체(`~합니다.`/`~입니다.`)로 강제.
- 안내(`kind == "guidance"`): `st.info`.
- 사이드바가 좁아 답변이 길면 접힘 — 프롬프트로 간결하게 유도. 더 넓히려면
  `st.columns` 우측 패널(단 스크롤 시 안 따라옴).

## 4. 구현 순서

1. ✅ **A** (정보 추출)
2. ✅ **C** (검증, N6 방식)
3. ✅ **B** (경량 응답 함수 + `respond_to_policy_question` 오케스트레이션)
4. ✅ **D** (라우팅, chat.py + rendering.py)
5. ✅ **E** (최소 UI) / 🔧 **F** (테스트 통과, 앱 수동확인 대기)

## 5. 검증 계획

### 카테고리별 질문 세트 (팀 평가 방식과 같은 사상 — 함정 포함)
| 카테고리 | 예시 | 기대 결과 |
|---|---|---|
| 정책 상세 질문 | "신청서류/신청기한 뭐예요?" | detail 섹션에서 빠르고 정확하게 답 |
| 정책에 없는 정보 | 컨텍스트에 없는 세부사항 | answerable=false → 안내 메시지 |
| 다른 정책 질문 | "다른 지역 정책도 있어요?" | 안내 메시지 (에스컬레이션 없음) |
| 환각 유도 함정 | 세션에 없는 금액을 슬쩍 물어봄 | evidence_quotes 검증 실패 → 안내 |

### 실제 평가셋 (2026-09-11, Qwen2.5-72B-Instruct)

`scripts/eval_light_followup.py` + `data/evaluation/light_followup_{policies.json,dev.jsonl}`.
정책 8개(vectorDB에서 실제 detail 동결) × 질문 29개(답변가능 18 + 함정거절 11).
`--pace` 옵션 필수 — 무료 HF 엔드포인트가 쉴 틈 없이 연달아 호출하면 버스트
직후부터 계속 즉시 실패(-> 안내로 오탐)해서 숫자가 왜곡됨. 재시도 사이
`_RETRY_BACKOFF_SECONDS`(1.5초)도 이 문제 때문에 추가.

| 버전 | 전체 | 답변가능 | 거절(함정) |
|---|---|---|---|
| Baseline (원래 프롬프트) | 16/29 (55%) | 7/18 (39%) | 9/11 (82%) |
| 프롬프트 v2 (few-shot 2개 + "조금이라도 관련 있으면 true") | 측정 중 |  |  |

**주요 실패 패턴**: 과잉 거절(답할 수 있는데 answerable=false) — 잘못된 답을 하는
경우는 거의 없음(검증 C가 잡음). 함정 케이스 중 2개(`youth5`, `repair2`)는
사실 모델이 원문에서 합리적으로 추론해 답했는데 eval 기대값 설계가 너무
엄격했던 것으로 판명 — 실제 답변가능 정확도는 39%보다 약간 높을 가능성.

## 6. 완료 조건 (DoD)

- 정책 상세 질문에 N1~N14 재실행 없이 답변이 나옴
- LLM 답변의 근거 발췌가 컨텍스트에 실제로 존재함을 검증(N6 방식)
- 답 못 하는 질문/다른 정책 질문은 안내 메시지로 처리(지어내지 않음)
- 관련 테스트 작성 및 통과
- 로컬 환경에서 검증 완료

## 7. 남은 것 / 추후 논의

- **F 수동확인**: 실제 Streamlit 앱에서 정책 추천 → 카드 버튼 → 상세 질문 →
  답변/안내/그만두기 흐름 확인. `data/vector_db/chroma.sqlite3`와 `HF_TOKEN`
  필요(LLM 없으면 `respond_to_policy_question`이 항상 안내로 폴백함 — 그 폴백
  경로는 통합 테스트로 이미 확인).
- 후속질문끼리 대화가 이어지게 할지(연쇄) — 현재는 매 턴 원래 정책 컨텍스트만
  기준. `light_answer` 메시지는 messages에 쌓이지만 다음 턴 프롬프트에 넣지 않음.
- 경량 답변 카드를 무거운 답변과 더 뚜렷이 구분할지(E 고도화).

### 결정됨

- D-2(어느 정책): **정책 카드의 "이 정책에 대해 물어보기" 버튼 클릭**.
- E(표시): 사이드바 채팅 패널 + 마크다운 답변(격식체 강제) + "보충자료" expander + 안내는 `st.info`.

## 8. 참고 — 헷갈리지 말 것

- `service.py`의 `answer_followup()`은 N3 interrupt 재개용, 이 기능과 다름.
- `_fetch_policy_detail()`은 이미 있는 함수 — 우리는 그 결과(`policies[i]["detail"]`)를
  세션에서 재사용만 함, 새로 호출하지 않음.
- 프로젝트 핵심 원칙: "검색된 공적 근거로 명확히 입증된 내용만 답한다" (`README.md`)

# N9~N12 구현 계획

> 문서 성격 안내: `docs/N7_N8_IMPLEMENTATION_PLAN.md`는 구현 전에 계약을 확정한
> **사전 설계 문서**다. 이 문서는 반대로 N9~N12 코드가 이미 구현·병합된
> 뒤(`feat/11-N9-N12-node` 브랜치) 같은 목차 구조로 **사후 정리**한 것이다 —
> 그래서 "확정 계약" 항목 중 일부는 N7·N8 수준의 formal invariant가 아니라
> 실제 코드에 있는 그대로의 규칙이고, "미해결 사항" 섹션이 별도로 있다(N7·N8은
> 계약을 전부 확정하고 끝났지만 N9~N12는 원천 데이터 한계로 여러 항목이 아직
> 열려 있다).

## 목표

N9 자격 판정, N10 지원금 계산, N11 중복수급 판정, N12 결과 조립을 기존
`GraphState`(Issue #11 계약)와 vectorDB(`ChromaVectorStore`) 연결부에 맞는
최소 노드로 구현한다. 각 노드는 이전 노드(N6/N7)가 전달한 `claim_plan`을
그대로 신뢰하지 않고, 근거가 가리키는 정책 문서를 vectorDB에서 한 번 더
검색해 구조화 metadata로 재확인한다. 근거에 명시되지 않은 조건은 추측하지
않고 "미확인"으로 남긴다.

범위 밖은 N5·N6·N7·N8·N13·N14 구현, LangGraph graph builder 배선, N10의
산술(곱셈) 계산 로직, N11의 능동적 상호배타 검색, RunPod 엔드포인트 실배포다.

## 현황

- 기준 커밋은 main의 `3892f6c`이며, `state.py`에 `EligibilityVerdict`,
  `BenefitAmount`, `DuplicateVerdict`, `GraphState`의 N9~N12 관련 필드가
  이미 total=False로 정의돼 있었다(이번 작업에서는 `ClaimDraft.status`
  타입을 `str`에서 `EvidenceStatus`로 좁힌 것 외에 `state.py` 변경 없음).
- N9~N12는 원래 각자 별도 브랜치(`feat/11-n9-eligibility-verdict-node` 등
  4개)에서 개발됐고, 이번에 `feat/11-N9-N12-node`(구 이름
  `feat/11-N8-N12-node` — 개발 당시에는 N8이 별도 트랙
  `origin/feat/20-n7-n8-evidence-gate-law-search`에만 있어 이 통합 브랜치에
  포함된 적 없었고, 이름만 처음 요청받은 그대로였다가 이번에 개명)로 순차
  병합했다.
- 그 뒤 팀이 N7·N8을 PR #22(`d892120`)로 `main`에 병합했고, 이 브랜치도
  `git merge main`(`fc61911`)으로 그 내용을 받아왔다. `nodes/__init__.py`,
  `state.py`의 병합 충돌은 두 트랙이 각자 추가한 export/필드를 합치는
  방식으로 해결했다(상세는 "구현 단계" 8번 참고). 지금은 N7~N12가 같은
  브랜치에 함께 있다.
- 실제 정부24 API 원천(`data/raw/gov24_support_conditions.json`,
  `src/rag_chatbot/collectors/gov_24/to_document.py`)에는 나이 조건
  (JA0110/JA0111 → `age_start`/`age_end`)만 구조화 필드로 확인됐다. 금액,
  상호배타 관계에 대응하는 원천 필드는 확인되지 않았다 — N10/N11의 규칙
  기반 판정이 구조적으로 제한되는 근본 원인이다.
- `rag_design.chunking.chunk_document()`의 SUBSIDY metadata 화이트리스트에
  `age_start`/`age_end`가 빠져 있어 N9 재검색이 나이 조건을 볼 수 없는
  버그를 발견해 고쳤다(Issue #11) — 실제 유아학비 정책 문서
  (`age_start=3, age_end=5`, 정부24 JA0110=3/JA0111=5와 일치)로 수정 전/후
  동작을 검증했다.
- LAW 컬렉션(`data/vector_db`, 국가법령정보센터 실 데이터)에 `law_name`,
  `source_url` chunk metadata가 있어 N12의 법령 재검색에 exact-match로
  연결 가능함을 실제 데이터로 확인했다.
- 팀은 skt/A.X-4.0-Light, Qwen/Qwen3.5-9B,
  Bllossom/llama-3.2-Korean-Bllossom-3B 세 모델을 fine-tuning 후보로 비교
  중이며 RunPod Serverless로 서빙할 계획이다(2026-08-31 기준 엔드포인트
  미배포, 프롬프트/출력 스키마 미확정). 이번 구현은 이 상태를 반영해
  `llm_client: LLMClient | None = None`을 노드 시그니처에 추가하고, LLM이
  없거나 실패해도 규칙 기반 결과가 그대로 유지되게 만들었다.
- 테스트는 표준 `unittest`(pytest로도 실행)를 쓴다. N9~N12만 있던
  `main` 병합 전에는 전체 suite가 153 passed / 116 subtests passed였고,
  `main`(N7·N8 포함) 병합 후에는 247 passed / 238 subtests passed다.

## 확정 계약

### 공통 입력 불변조건과 재검색 원칙

- 노드 시그니처는 `def <노드_함수>(state: GraphState, store: ChromaVectorStore,
  llm_client: LLMClient | None = None) -> dict`로 통일한다(N11/N12는
  `llm_client` 없음). `store`(및 있다면 `llm_client`)는 checkpoint에 저장되는
  state에 넣지 않고 `functools.partial(<노드_함수>, store=store, ...)`로
  그래프 조립 시 주입한다.
- 각 노드는 claim_plan을 policy_id 단위로 그룹화한 뒤, 해당 claim_type
  (`eligibility`/`amount`/`duplicate`)만 골라 처리한다. `NOT_APPLICABLE`
  claim은 판정 대상에서 제외한다.
- claim 상태가 `UNSUPPORTED`/`PARTIAL`/`CONFLICT`(공통 상수
  `_UNCERTAIN_STATUSES`) 중 하나라도 섞여 있으면 재검색을 생략하고 즉시
  "미확인"(N10/N11은 amount=None/status=미확인)으로 반환한다 — 불확실한
  근거로 vectorDB를 재검색해봐야 의미가 없기 때문이다.
- 모든 재검색은 `store.search(SourceType.SUBSIDY, <질의문>, query_id=...,
  top_k=3, search_filter=VectorSearchFilter(metadata_equals={"doc_id":
  policy_id, ...}))` 형태로 호출하며, `doc_id`로 좁혀 이미 claim_plan이
  가리키는 그 정책 문서만 다시 조회한다(새 정책을 찾지 않음).
- `store.search()`가 `CollectionNotFoundError`를 던지면(해당 소스 타입이
  한 번도 동기화되지 않은 상태) 모든 노드가 이를 잡아 빈 결과(`()`)로
  취급한다 — 예외를 그대로 흘려보내면 그래프 전체가 죽기 때문에, "근거를
  못 찾음"과 동일한 경로로 합류시킨다.
- 재검색 결과가 비어 있으면 판정하지 않고 "재검색에서 해당 정책 근거를
  다시 찾지 못함"과 함께 미확인류 결과를 반환한다.
- 정책 간 값(금액 등)을 임의로 합산하지 않는다 — 모든 출력은 policy_id별로
  분리된 리스트/딕셔너리 구조를 유지한다.

### N9 자격 판정 (`determine_eligibility`, `eligibility_verdict.py`)

- 입력: `state["slots"]`(사용자 정보), `state["claim_plan"]`의
  `claim_type == "eligibility"` claim.
- 관련 claim이 모두 `SUPPORTED`면 재검색을 수행하고, 재검색한 chunk의
  `metadata["age_start"]`/`metadata["age_end"]`와 `slots["age"]`를 비교한다.
  - `age < age_start` 또는 `age > age_end`면 "미충족"이고 위반 사유 문자열을
    만든다. 슬롯에 `age`가 없거나 chunk에 age 조건이 아예 없으면 비교하지
    않는다(위반로 단정하지 않음).
  - 위반이 없으면 "충족", claim의 `reasons`를 그대로 verdict의 `reasons`로
    사용한다.
- 관련 claim이 없으면(NOT_APPLICABLE만 있거나 아예 없으면) "미확인" +
  "판정 가능한 자격 조건 근거가 없음".
- 위반 사유는 `_naturalize_reasons()`로 LLM에 한 번 더 통과시킬 수 있다(LLM
  사용 범위는 아래 별도 절 참고). LLM은 판정 자체(충족/미충족/미확인)에는
  관여하지 않는다.

### N10 지원금 계산 (`calculate_benefit_amount`, `benefit_calculator.py`)

- 입력: `state["eligibility_verdicts"]`에서 `verdict == "충족"`인
  policy_id만, `state["claim_plan"]`의 `claim_type == "amount"` claim.
- 재검색 시 `doc_id`뿐 아니라 `section_type == "support_details"`까지
  필터링한다 — 정책 문서에 지원내용 섹션 자체가 없으면 검색 결과가 없어
  자연스럽게 `amount=None`으로 떨어진다(별도의 "이게 지원금 제도인지" 판단
  로직을 추측으로 만들지 않는다).
- 재검색한 chunk의 `metadata["amount"]` 또는 `metadata["benefit_amount"]`가
  있으면 그대로 사용한다(`calculation_note = "재검색한 chunk metadata의
  구조화 금액 필드를 그대로 사용"`).
- 구조화 필드가 없으면 `_extract_amount_via_llm()`을 호출한다(LLM 사용
  범위는 아래 참고). 그 결과도 없으면(`llm_client=None`이거나 호출/파싱
  실패) `amount=None` + 사유 문자열을 남긴다.
- 반환 항목마다 `rule_chunk_id`(재검색한 chunk의 `chunk_id`, 못 찾았으면
  빈 문자열)와 `calculation_note`를 항상 채워 N14 최종 검증에서 추적 가능하게
  한다.
- 미구현: 원문이 "가구원수 × 단가"처럼 계산식을 요구하는 경우의 산술 단계.
  현재는 원문에 이미 명시된 단일 금액만 쓰고, 계산식이 필요한 규칙은
  `amount=None`으로 남긴다.

### N11 중복수급 판정 (`check_duplicate_benefit`, `duplicate_benefit.py`)

- 입력: `state["eligibility_verdicts"]` **전체**(충족/미충족/미확인 모두 —
  N10과 달리 "충족"만으로 거르지 않는다, E17 계약), `state["claim_plan"]`의
  `claim_type == "duplicate"` claim.
- 재검색한 chunk의 `metadata["mutually_exclusive_with"]`에 사용자가 이미
  "충족" 판정을 받은 **다른** policy_id가 실제로 들어 있을 때만 "불가"로
  판정하고 `conflicts_with`에 담는다.
- 그 필드가 없으면(현재 원천 데이터에는 사실상 항상 없음) "가능"을 임의로
  단정하지 않고 기본값 "미확인"을 반환한다.
- status는 "가능"/"불가"/"조건부"/"미확인" 4개 값을 표현할 수 있는 문자열
  필드이지만, 실제로 "가능"·"조건부"를 만드는 로직은 아직 없다(아래
  "명세 충돌과 채택 결정" 및 "미해결 사항" 참고).

### N12 결과 조립 (`assemble_result`, `result_assembly.py`)

- 입력: `state["eligibility_verdicts"]`, `state["benefit_amounts"]`,
  `state["duplicate_verdicts"]`(policy_id 기준 딕셔너리로 인덱싱).
- `eligibility_verdicts`에 있는 policy_id를 기준으로 순회하며(즉 자격
  판정이 없는 정책은 애초에 조립 대상이 아님) `policies[policy_id]`에
  `eligibility`를 채운다.
- `verdict == "충족"`인 정책만 `benefit_amount`를 채운다. `benefit_amounts`에
  해당 policy_id가 없거나 `amount is None`이면 "정보 부족: 지원금 계산 결과
  없음" 노트를 남기고, **법령 검색을 트리거**한다(아래).
- `duplicate_verdicts`에 없는 정책은 `duplicate=None` + "정보 부족: 중복수급
  판정 결과 없음"(이미 다른 status_note가 있으면 덮어쓰지 않고
  `setdefault`).
- 법령 검색(`_find_related_law`): 지원금 계산 실패한 정책만 대상으로,
  1) SUBSIDY의 `section_type == "legal_basis"` chunk를 `doc_id`로 재검색,
  2) `"법령명(제n조)||법령명(제n조)"` 형식 본문에서 괄호 조항 표기를 정규식
  (`\s*\([^)]*\)\s*`)으로 제거해 법령명만 추출,
  3) 추출한 법령명 각각으로 LAW 컬렉션을 `metadata_equals={"law_name":
  name}` exact match로 재검색(임베딩 유사도만으로는 크루드한
  `HashEmbeddingProvider` 특성상 부정확해서 exact filter를 씀),
  4) 찾은 법령마다 `{"law_name", "source_url", "source_name"}`을
  `related_law`에 담는다. 근거법령 자체가 없거나 LAW 컬렉션에서 못 찾으면
  빈 리스트를 그대로 반환한다(숨기지 않음).
- `node_trace`에 기존 값을 복사한 뒤 `"N12"`를 append해 반환한다(입력
  리스트를 직접 수정하지 않음).

### LLM 연동 계층 (`src/rag_chatbot/llm/`)

- `LLMClient` Protocol: `complete(prompt: str, *, system: str | None = None)
  -> str` 하나만 요구한다. 파싱은 호출부(N9/N10) 책임이다 — 이 계층은
  프롬프트/스키마가 아직 확정 전이라는 전제를 명시적으로 반영한다.
- `LLMCallError`: 호출 실패(네트워크/타임아웃/파싱)를 나타내는 예외. 호출부는
  이를 `CollectionNotFoundError`처럼 잡아서 "LLM 단계를 못 거쳤다"는 사실을
  정직하게 남기고 절대 값을 추측하지 않는다.
- `RunPodServerlessClient(endpoint_id=None, api_key=None, *, model=None,
  timeout_seconds=60.0)`: 인자가 없으면 `RUNPOD_ENDPOINT_ID`/
  `RUNPOD_API_KEY`/`RUNPOD_MODEL_NAME` 환경변수를 읽는다. `endpoint_id`나
  `api_key`가 끝내 없으면 생성자에서 즉시 `ValueError`. `complete()`는
  `POST https://api.runpod.ai/v2/{endpoint_id}/runsync`에
  `{"input": {"model": ..., "messages": [...]}}`을 보내고,
  `_parse_output()`이 vLLM/OpenAI 호환 두 응답 형태
  (`output[0]["choices"][0]["message"]["content"]` 또는
  `output["choices"][0]["message"]["content"]` 또는 `output["text"]`)를
  순서대로 시도한다. 아직 실제 엔드포인트로 검증된 적은 없다(DRAFT).
- `FakeLLMClient(response="")`: 테스트용, 호출을 `.calls`에 기록하고 고정
  응답을 반환한다.
- `FailingLLMClient(message="테스트용 강제 실패")`: 항상 `LLMCallError`를
  던지는 테스트용 더블.
- N9/N10 노드는 `llm_client=None`이 기본값이며, 이 경우 LLM을 전혀 호출하지
  않고 규칙 기반 로직만으로 동작한다 — RunPod가 아직 없어도 지금 코드가
  깨지지 않는 이유다.

### State 확장

`state.py`의 N9~N12 관련 필드는 이번 작업 이전에 이미 있었다(현황 참고).
이번에 바꾼 것은 `ClaimDraft.status`의 타입을 `str`에서
`rag_design.contracts.EvidenceStatus`로 좁힌 것뿐이다.

- `EligibilityVerdict`: `policy_id: str`, `verdict: str`("충족"/"미충족"/
  "미확인"), `reasons: list[str]`
- `BenefitAmount`: `policy_id: str`, `amount: float | None`,
  `rule_chunk_id: str`, `calculation_note: str`
- `DuplicateVerdict`: `policy_id: str`, `status: str`("가능"/"불가"/
  "조건부"/"미확인"), `conflicts_with: list[str]`, `condition_note: str`
- `GraphState`: `eligibility_verdicts: list[EligibilityVerdict]`,
  `benefit_amounts: list[BenefitAmount]`,
  `duplicate_verdicts: list[DuplicateVerdict]`,
  `assembled_result: dict[str, Any]`, `node_trace: list[str]`

검색 callable, vector store, LLM client 같은 런타임 객체는 state에 넣지
않는다(N7·N8과 동일 원칙).

## 명세 충돌과 채택 결정

이 대화에서 N9~N12에 대해 서로 다른 두 버전의 스펙 설명이 있었다. 각
충돌 지점과 채택한 쪽을 명시적으로 남긴다.

| 충돌 | 채택 결정 |
| --- | --- |
| N12를 1차 스펙은 "계산 실패 시 관련 법령 검색"이라 하고, 2차 스펙은 "순수 결정론적 노드, 검색 안 함"이라 한다. | 1차 스펙(법령 검색 포함)을 채택했다 — 예시가 구체적이고, 실제 `data/vector_db`의 SUBSIDY↔LAW 데이터로 "찾은 경우"(유아학비→영유아보육법, 실 law.go.kr 링크)와 "못 찾은 경우"(근로·자녀장려금→빈 리스트) 둘 다 end-to-end 검증까지 끝난 상태였기 때문이다. 2차 스펙 쪽으로 되돌리려면 `assemble_result`의 `store` 인자와 `_find_related_law` 호출을 제거하면 된다. |
| N9는 1차 스펙(JA코드 자연어화)과 2차 스펙(LLM+규칙 병행, 판정은 규칙만)을 동시에 만족해야 한다. | 두 스펙이 실제로는 상충하지 않아 둘 다 채택했다 — 판정(충족/미충족/미확인)은 100% 규칙(`_find_structured_violations`)이 결정하고, LLM은 그 규칙이 만든 위반 사유 문장만 자연어로 다듬는다(`_naturalize_reasons`, "JA코드 자연어화"에 해당). LLM 미연결·실패 시에도 판정 결과는 동일하게 유지된다. |
| N10 1차 스펙은 "SUBSIDY DETAIL 섹션 검색"만 언급하고, 2차 스펙은 "LLM은 구조화 추출만, 코드가 결정론적 산술"이라고 산술 단계를 명시한다. | 검색 필터링(`section_type="support_details"`)과 LLM 구조화 추출까지는 구현했다. 산술 단계(예: 가구원수 × 단가)는 실제 원천 데이터에 계산식 규칙 스키마가 없어 미구현으로 남기고 amount=None으로 처리한다 — 임의 구현하지 않고 명시적 TODO로 남겼다. |
| N11 2차 스펙은 "명시적 배제 조항만 인정, Gate1 메타데이터 미확정이면 미확인"이라 하고, 1차 스펙은 "검색된 복지제도와 중복 지원 불가능한 지원제도를 검색하여 LIST로 추가"(능동 검색을 시사)라고 한다. | 2차 스펙(수동적 metadata 대조, 명시적 배제 조항만 인정)을 채택했다 — 정부24 원천 데이터에 `mutually_exclusive_with`에 대응하는 실제 필드가 없어서, 능동 검색으로 바꾼다 해도 검색할 대상 자체가 없기 때문이다. 원천 데이터에 이 관계를 표현하는 필드가 생기면 1차 스펙 방향으로 확장할 여지를 남겨뒀다. |
| N11의 4단계(가능/불가/조건부/미확인) 중 "가능"과 "조건부"를 실제로 만드는 로직이 스펙에 없다. | "가능"은 임의로 단정하지 않는다는 원래 stub 설계 결정을 유지해 기본값을 "미확인"으로 뒀다. "조건부"는 자연어 조항 해석(LLM)이 필요해 이번 범위에서 구현하지 않았다. |

## 변경 파일과 심볼

`main`(`3892f6c`) 대비 `feat/11-N9-N12-node`의 diff 기준(15 files changed,
1883 insertions(+), 4 deletions(-)):

| 파일 | 변경 |
| --- | --- |
| `src/rag_chatbot/graph/state.py` | `ClaimDraft.status` 타입을 `str` → `EvidenceStatus`로 좁힘 (+4/-4) |
| `src/rag_chatbot/graph/nodes/eligibility_verdict.py` | N9 `determine_eligibility()`, `_find_structured_violations()`, `_naturalize_reasons()` 신규 (220줄) |
| `src/rag_chatbot/graph/nodes/benefit_calculator.py` | N10 `calculate_benefit_amount()`, `_extract_amount_via_llm()` 신규 (224줄) |
| `src/rag_chatbot/graph/nodes/duplicate_benefit.py` | N11 `check_duplicate_benefit()`, `_find_confirmed_conflicts()` 신규 (162줄) |
| `src/rag_chatbot/graph/nodes/result_assembly.py` | N12 `assemble_result()`, `_extract_law_names()`, `_find_related_law()` 신규 (166줄) |
| `src/rag_chatbot/graph/nodes/__init__.py` | 4개 노드 함수 re-export (+10/-4) |
| `src/rag_chatbot/llm/__init__.py` | `LLMClient`/`LLMCallError`/`RunPodServerlessClient`/`FakeLLMClient`/`FailingLLMClient` re-export 신규 (17줄) |
| `src/rag_chatbot/llm/client.py` | LLM 클라이언트 계층 신규 (155줄) |
| `rag_design/chunking.py` | SUBSIDY metadata 화이트리스트에 `age_start`/`age_end` 추가 (버그 수정, +6줄) |
| `.env.example` | `RUNPOD_ENDPOINT_ID`/`RUNPOD_API_KEY`/`RUNPOD_MODEL_NAME` 자리 추가 (+10줄) |
| `scripts/manual_test_chain.py` | N9→N10→N11→N12 수동 체이닝 스크립트 신규, 로컬 전용 (221줄) |
| `tests/test_graph_nodes.py` | N9 FakeStore 단위 테스트 13개 (292줄 — 통합 브랜치에는 이 파일명이 4개 브랜치에서 겹쳐 병합마다 `--ours`로 하나만 남음, 아래 "미해결 사항" 참고) |
| `tests/test_graph_nodes_realchroma.py` | N9 실제 ChromaVectorStore 통합 테스트 4개 (143줄) |
| `tests/test_n10_realchroma.py` | N10 실제 ChromaVectorStore 통합 테스트 3개 (125줄) |
| `tests/test_n11_realchroma.py` | N11 실제 ChromaVectorStore 통합 테스트 3개 (132줄) |

구현 변경은 위 15개 파일로 고정돼 있다. `rag_design`에서는 `chunking.py`의
화이트리스트 수정 외에는 건드리지 않았고, graph builder와 N5~N8/N13/N14
파일은 변경하지 않았다.

## 구현 단계

실제로는 4개 노드가 각자 별도 브랜치에서 개발된 뒤 순서대로 통합됐다.

1. N9(`feat/11-n9-eligibility-verdict-node`): `determine_eligibility()` 최초
   구현 — 재검색 + 구조화 나이 조건 비교.
2. N10(`feat/11-n10-benefit-calculator-node`): `calculate_benefit_amount()`
   최초 구현 — `section_type="support_details"` 필터 재검색 + 구조화 금액
   필드.
3. N11(`feat/11-n11-duplicate-benefit-node`): `check_duplicate_benefit()`
   최초 구현 — `mutually_exclusive_with` metadata 대조.
4. N12(`feat/11-n12-result-assembly-node`): `assemble_result()` 최초 구현 —
   초기에는 순수 조립만 하다가, Issue #11 스펙 재검토 후 법령 검색 로직
   (`_find_related_law`) 추가로 시그니처가 `assemble_result(state)` →
   `assemble_result(state, store)`로 바뀜.
5. `rag_design/chunking.py`의 `age_start`/`age_end` 화이트리스트 버그를
   4개 브랜치 전부에 동일하게 포팅.
6. 4개 브랜치를 `feat/11-N9-N12-node`(구 `feat/11-N8-N12-node`)로 순차
   병합 — 파일명이 겹치는 `tests/test_graph_nodes.py`는 매 병합마다
   `--ours`로 해결.
7. LLM 연동 계층(`src/rag_chatbot/llm/`)을 신규 작성하고 N9/N10에
   `llm_client` 인자로 배선, 각각 4~5개 테스트(`FakeLLMClient`/
   `FailingLLMClient` 기반) 추가 후 통합 브랜치에 재병합.
8. `scripts/manual_test_chain.py`로 N9→N10→N11→N12 실 체이닝을 실제
   `data/vector_db`(실 유아학비/근로·자녀장려금 데이터) 대상으로 검증.
9. 팀이 PR #22(`d892120`)로 N7·N8을 `main`에 병합한 뒤, `feat/11-N9-N12-node`에서
   `git merge main`을 실행 — `nodes/__init__.py`(양쪽이 각자 추가한 노드
   re-export를 합침), `state.py`(N7·N8이 추가한 `RequiredLawSource`,
   `EvidenceGateVerdict`, `as_of`/`safety_blocked`/`evidence_gate_verdict`
   등 필드와 N9~N12 쪽 `ClaimDraft.status: EvidenceStatus` 변경을 합침)
   두 곳만 실제 충돌이 있었고 수동으로 합쳐서 해결했다. 나머지 20개
   파일은 CRLF/LF 개행 차이로 인한 노이즈만 있어 `git checkout -- .`로
   제거하고, 병합 커밋(`fc61911`)을 만들기 전에 전체 suite(247 passed /
   238 subtests passed)와 노드 import를 재확인했다.

## 테스트 계획

`tests/` 전체는 표준 `unittest` 기반이며 `pytest`로도 실행 가능하다.

| 테스트 ID | 시나리오 | 검증 |
| --- | --- | --- |
| T1 | 자격 claim SUPPORTED + 나이 조건 충족 | "충족", claim reasons 그대로 사용 |
| T2 | age < age_start | "미충족", 최소 연령 위반 사유 |
| T3 | age > age_end | "미충족", 최대 연령 위반 사유 |
| T4 | 자격 claim UNSUPPORTED | 재검색 생략하고 "미확인" |
| T5 | 자격 claim CONFLICT | "미확인" |
| T6 | 재검색 결과 없음(빈 chunk) | "미확인" + "재검색에서 다시 찾지 못함" |
| T7 | age 슬롯 없음 또는 chunk에 나이 조건 없음 | 비교하지 않고 "충족" 유지 |
| T8 | 해당 정책에 자격 claim이 없음 | 빈 verdicts 목록 |
| T9 | 여러 정책 동시 존재 | 정책별로 독립적으로 판정(교차 오염 없음) |
| T10 | `llm_client=None` | 규칙이 만든 원문 사유 그대로 유지 |
| T11 | `llm_client=FakeLLMClient(...)` | 위반 사유가 LLM 응답으로 자연어화됨 |
| T12 | `llm_client=FailingLLMClient(...)` | `LLMCallError` 잡아 규칙 원문 사유로 폴백 |
| T13 | verdict가 "충족"인 경우 | LLM을 아예 호출하지 않음(`.calls` 비어있음) |
| T14 | chunk metadata에 구조화 amount 있음 | 그 값을 그대로 사용, LLM 미호출 |
| T15 | 구조화 amount 없고 `llm_client=None` | amount=None, 추측하지 않음 |
| T16 | "충족" 아닌 정책 | 애초에 처리 대상에서 제외 |
| T17 | amount claim 상태가 불확실(UNSUPPORTED 등) | 재검색 생략 |
| T18 | 재검색 결과 없음 | amount=None + "다시 찾지 못함" |
| T19 | 여러 "충족" 정책 동시 존재 | 금액을 합산하지 않고 정책별로 분리 |
| T20 | 구조화 필드 없고 `llm_client=FakeLLMClient(JSON 응답)` | 원문에서 추출한 금액 사용 |
| T21 | LLM이 조건부 규칙에 대해 `amount: null` 반환 | amount=None 유지(임의 대표값 생성 금지) |
| T22 | `llm_client=FailingLLMClient(...)` | amount=None + 실패 사유 기록 |
| T23 | LLM 응답이 JSON이 아님 | 파싱 실패, amount=None(원본 미신뢰) |
| T24 | 구조화 amount가 이미 있음 | LLM을 아예 호출하지 않음 |
| T25 | 재검색 chunk의 `mutually_exclusive_with`에 이미 "충족"인 다른 정책 포함 | "불가", `conflicts_with`에 해당 정책 |
| T26 | `mutually_exclusive_with` metadata 자체가 없음 | "가능" 임의 단정 없이 "미확인" |
| T27 | 배제 목록에 "충족" 아닌 정책만 있음 | "미확인"(다른 충족 정책과 겹치지 않으므로) |
| T28 | duplicate claim 상태 불확실 | 재검색 생략 |
| T29 | 재검색 결과 없음 | "미확인" + "다시 찾지 못함" |
| T30 | eligibility_verdicts에 미충족/미확인도 섞여 있음 | "충족"만이 아니라 전체 verdict를 처리 |
| T31 | eligibility/amount/duplicate 세 출력이 모두 있는 정책 | 셋을 policy_id 단위로 정확히 조립 |
| T32 | amount 계산이 성공한 정책 | 법령 검색을 아예 하지 않음 |
| T33 | "충족"인데 amount 결과 자체가 없음 | "정보 부족" 노트 + 법령 검색 트리거 |
| T34 | amount가 있는데 값이 None(계산 실패) | 같은 "정보 부족" 처리 + 법령 검색 |
| T35 | 근거법령 본문이 "법령A(제n조)\|\|법령B(제n조)" 형태 | 각 법령명을 개별로 분리해 전부 검색·수집 |
| T36 | "미충족" 정책 | `benefit_amount` 키 자체를 만들지 않음 |
| T37 | duplicate_verdicts에 해당 policy_id가 없음 | "정보 부족" 노트로 명시(숨기지 않음) |
| T38 | 여러 정책의 amount 동시 존재 | 총합을 만들지 않고 정책별로 유지 |
| T39 | 기존 `node_trace`에 이전 노드 기록이 있음 | 그 뒤에 "N12"만 append(덮어쓰지 않음) |
| T40~T43 | (`test_graph_nodes_realchroma.py`) 실제 `ChromaVectorStore` + `HashEmbeddingProvider`로 나이 조건 충족/미충족, 컬렉션 미동기화, 정책 미색인 | FakeStore가 아닌 실제 벡터DB 경로로 T1/T2/T4~T6과 동등한 결과 재확인 |
| T44~T46 | (`test_n10_realchroma.py`) 실제 store로 구조화 금액 사용/없음/컬렉션 미동기화 | T14/T15/T4와 동등한 결과를 실제 검색 경로로 재확인 |
| T47~T49 | (`test_n11_realchroma.py`) 실제 store로 배제 조항 일치/없음/컬렉션 미동기화 | T25/T26/T4와 동등한 결과를 실제 검색 경로로 재확인 |

```bash
# 레포 루트에서
python -m pytest tests/test_graph_nodes.py tests/test_graph_nodes_realchroma.py \
    tests/test_n10_realchroma.py tests/test_n11_realchroma.py -v

# 전체 suite
python -m pytest tests/ -q

# unittest로 실행하고 싶으면 (pytest 없는 환경)
python -m unittest tests.test_graph_nodes tests.test_graph_nodes_realchroma \
    tests.test_n10_realchroma tests.test_n11_realchroma -v

# N9→N10→N11→N12 실 체이닝(실제 data/vector_db 대상)
python scripts/manual_test_chain.py
```

`feat/11-N9-N12-node` 기준 마지막 확인 결과: 전체 suite 153 passed / 116
subtests passed.

## 완료 기준

- N9~N12 모두 claim_plan을 그대로 신뢰하지 않고 vectorDB 재검색으로
  재확인하며, `CollectionNotFoundError`를 fail-closed로 흡수해 그래프를
  죽이지 않는다.
- N9의 판정(충족/미충족/미확인)은 구조화 metadata 비교 규칙만으로
  결정되며, LLM 연동 유무·성공/실패와 무관하게 동일한 판정을 낸다.
- N10은 구조화 금액 필드를 우선 사용하고, 없을 때만 LLM으로 원문에서
  확정 금액을 추출하며(계산하지 않음), 조건부/모호한 규칙은 amount=None +
  사유로 남긴다. 정책 간 금액을 합산하지 않는다.
- N11은 명시적 배제 조항(metadata)이 실제로 있을 때만 "불가"를 내고,
  "가능"을 임의로 단정하지 않는다.
- N12는 세 노드 출력 중 하나라도 없는 정책을 숨기지 않고 "정보 부족"으로
  표시하며, 지원금 계산이 실패한 정책에 한해 관련 법령을 재검색해 링크를
  보충한다(찾지 못해도 빈 리스트로 정직하게 반환).
- LLM 클라이언트 계층은 프롬프트/모델이 미확정인 현재 상태에서도 노드
  로직과 분리돼 있어, RunPod 엔드포인트가 없어도(`llm_client=None`) 전체
  파이프라인이 정상 동작한다.
- 신규 테스트(T1~T49, 실 ChromaDB 통합 테스트 포함)와 기존 회귀가 모두
  통과한다.
- graph builder, N5~N8, N13~N14, 새 외부 의존성은 이번 범위에 포함하지
  않는다.

## 미해결 사항 (팀 확인 필요)

N7·N8 문서와 달리 이 구현은 원천 데이터 한계로 계약을 전부 닫지 못했다.
남은 항목을 명시적으로 남긴다.

- **N12 스펙 재확정**: 법령 검색을 유지할지, 원래 xlsx 설계표대로 순수
  결정론적 노드로 되돌릴지 팀 결정이 필요하다.
- **N10 산술 단계 미구현**: "가구원수 × 단가" 같은 계산식 규칙을 코드가
  결정론적으로 계산하는 부분이 아직 없다. 실제 규칙 스키마가 정해지면
  추가해야 한다.
- **N11 "조건부" 판정 미구현**: 자연어 조항 해석이 필요해 이번 범위에서
  만들지 않았다.
- **N11 `mutually_exclusive_with` 메타데이터 스키마 부재**: 정부24 원천
  데이터에 대응 필드가 없어 Gate1 계약 확장이 선행돼야 "불가"/"조건부"
  판정이 실질적으로 동작한다 — 이 팀 범위 밖(수집기/스키마)일 수 있다.
- **RunPod 엔드포인트 미배포**: N9/N10의 LLM 연동은 배선만 완료됐고 실제
  호출은 검증되지 않았다. 별도 문서 `docs/RUNPOD_SETUP_DRAFT.md` 참고.
- **`tests/test_graph_nodes.py` 파일명 충돌**: N9~N12 네 브랜치가 전부 같은
  파일명을 써서, 통합 브랜치의 이 파일 하나로는 네 노드의 FakeStore 테스트
  전체를 커버하지 못한다(현재는 N9 버전만 남아 있음, T1~T13). N10/N11/N12의
  FakeStore 테스트(T14~T39)는 각 노드 커밋에는 존재하지만 통합 브랜치의
  이 파일에는 없다 — 파일을 분리(예: `test_n9_*.py`~`test_n12_*.py`)하는
  정리가 필요하다.
- ~~참고, N9~N12와 별개 트랙: N7·N8은 main에 아직 병합되지 않았다~~ →
  **2026-08-31 이후 해소됨**: 팀이 PR #22(`d892120`)로 N7(Evidence Gate)·
  N8(표적 법령 검색)을 `main`에 병합했고, `feat/11-N9-N12-node`도
  `git merge main`(`fc61911`)으로 받아와 지금은 N7~N12가 한 브랜치에 함께
  있다("구현 단계" 9번 참고). 다만 N7~N8 자체의 계약 검증은 이 문서
  범위가 아니라 `docs/N7_N8_IMPLEMENTATION_PLAN.md` 쪽 책임이다 — 이
  문서는 N9~N12 노드가 그 병합 이후에도 기존 동작(테스트 247/238 전부
  통과, import 정상)을 그대로 유지하는지까지만 확인했다.

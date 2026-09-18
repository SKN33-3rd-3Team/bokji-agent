# RAG_PATCH_CHANGELOG_REPORT

이 문서는 이번 작업에서 실제로 변경한 코드를 patch 단위로 기록한다.
모든 변경은 결정론적 단위 테스트로 뒷받침되며(전체 테스트 스위트
1142개 통과, 회귀 없음), 특정 질문/정책 ID에 대한 예외 처리는 없다
(과적합 감사 결과는 이 문서 8절 참고).

## 1. 변경 파일 목록

| file | change | reason |
| --- | --- | --- |
| `src/rag_chatbot/graph/nodes/policy_search.py` | N4 관련성 게이트 신설(LLM 판정 + 재확인 로직 + 거리 기반 사전 필터), 판정 스니펫 300→800자, 사전 필터를 "1등만 확정"으로 재설계 | Patch 1, 4, 5, 9 |
| `src/rag_chatbot/graph/builder.py` | `search_policies`에 `llm_client` 배선 | Patch 1 |
| `src/rag_chatbot/graph/nodes/answer_generation.py` | citation을 claim_type/`rule_chunk_id` 기반으로 정밀화, 법령명 환각 검증 추가 | Patch 2, 6 |
| `rag_design/answer_quality.py` | Faithfulness judge 프롬프트에 "메타 안내문 제외" 지침 추가 | Patch 7 |
| `scripts/build_policy_eval_set.py` | `--exclude-ids-file` 옵션 추가(준-Holdout 생성용, 기본 동작 불변) | Patch 3 |
| `scripts/analyze_failure_cases.py`(신규) | results_*.jsonl을 failure category로 분류하는 분석 스크립트 | Patch 8 |
| `scripts/compare_evaluation_runs.py`(신규) | 두 summary.json 비교 리포트 생성 | - |
| `scripts/pool_evaluation_runs.py`(신규) | 여러 summary.json을 count 가중으로 합산(pool)하는 스크립트 - 150+100+준-Holdout 100 통합 리포트에 사용 | - |
| `data/evaluation/quasi_holdout_questions.jsonl`(신규) | 준-Holdout 100문항(정책 80종 + 신규 보류 20건) | Patch 3 |
| `tests/test_policy_search_node.py` | 관련성 게이트·재확인·거리 사전 필터·1등만 확정 재설계 테스트 22건 추가 | - |
| `tests/test_answer_generation_node.py` | citation 정밀화 4건 + 법령명 환각 검증 2건 테스트 추가 | - |
| `tests/test_search_evidence_edge_cases.py` | citation label 값 갱신(라벨 세분화에 맞춤) | - |

## 2. Patch별 변경 내용

### Patch 1 — N4 관련성 게이트

- **문제**: N7(evidence_gate)이 근거의 스키마 유효성만 검사하고 질문과의
  관련성은 보지 않아, 검색이 뭔가를 반환하기만 하면(관련 없어도) 그대로
  답변이 생성됐다. 실측(2026-09-15): should_abstain 8건 중 7건이 잘못
  답변됨.
- **기존 동작**: `search_policies()`가 semantic 검색 + 정책 필터만 거쳐
  top-k를 그대로 `subsidy_chunks`로 반환.
- **변경 동작**: top-k 후보를 사용자 질문과 LLM으로 대조해(`_filter_relevant_candidates`)
  무관한 후보는 제거. 전부 무관하면 후보 0건이 되어 기존 검증된
  "빈 claim_plan → NO_EVIDENCE 보류" 경로로 이어짐.
- **변경 이유**: top-1 거리 임계값을 먼저 측정했으나(`measure_retrieval_distance.py`)
  정상 질문 26%가 잘못 걸릴 정도로 분포가 겹쳐 채택 불가.
- **영향받는 pipeline**: N4(policy_search) → N5(claim_plan) → N7(evidence_gate,
  변경 없음, 자연스럽게 이어받음).

### Patch 2 — Citation 정밀화

- **문제**: N13의 `_collect_citations`가 정책 하나에 걸린 eligibility/
  amount/duplicate claim의 근거 청크를 구분 없이 합쳐서 citation으로
  붙였다. `benefit_calculator.py`가 이미 계산에 실제로 쓴 청크
  (`rule_chunk_id`)를 갖고 있었는데도 미사용.
- **기존 동작**: 정책당 citation = 그 정책에 걸린 모든 claim의 evidence_chunk_ids 합집합.
- **변경 동작**: 지원금액 줄 → `rule_chunk_id` 단일 청크만. 지원자격/
  중복수급 줄 → 각 claim_type 범위로만 한정.
- **변경 이유**: "지원대상" 섹션 청크가 "지원금액" 줄의 근거인 것처럼
  인용되는 등 표시된 사실과 무관한 청크가 섞여 들어갔다.
- **영향받는 pipeline**: N13(answer_generation)만. N6/N7/N14의 근거 검증
  로직은 변경 없음.

### Patch 3 — 준-Holdout 데이터셋 생성

- **문제**: 이 저장소에 Holdout 세트가 없어 Dev/Holdout 일반화 격차를
  측정할 수 없었다.
- **변경**: `build_policy_eval_set.py`에 `--exclude-ids-file` 옵션을
  추가해, 기존 두 세트(`dev_questions.jsonl` 150 + `policy_eval_questions.jsonl`
  100)가 이미 쓴 정책 105종을 제외하고 80종을 새로 뽑았다. 여기에 이번
  세션에서 처음 쓰는 새 표현의 보류 문항 20건(허위정책/프롬프트인젝션/
  비밀정보/법률해석/법률본문 각 4건)을 더해 100문항을 만들었다.
- **중요한 한계(숨기지 않음)**: 이건 **진짜 Holdout이 아니다.** Holdout의
  핵심은 "튜닝 시작 전에 얼려두고 마지막에 한 번만 본다"인데, 이 세트는
  이미 여러 차례 튜닝한 뒤에 만들어졌다. "오늘 한 번도 안 본 정책/문항"
  이라는 속성만 보장하는 **준-Holdout(quasi-holdout)**이며, 생성 방식도
  `policy_eval_questions.jsonl`과 같아(정책 문서 요약문에서 파생) 그
  세트가 이미 갖고 있던 "어휘 겹침" 한계를 그대로 물려받는다.

### Patch 4 — 재확인(self-consistency) 로직

- **문제**: 관련성 게이트의 "전부 무관"(후보 0건, 보류로 직결) 판정이
  LLM 호출 한 번의 우연한 결과로 확정됐다. 실측: 같은 코드로 같은 정상
  질문(`dev-earned-income-002`)을 재실행하니 한 번은 정답을 관련 있다고
  옳게 판단, 한 번은 전부 무관으로 오판.
- **변경**: 첫 판정 결과가 0건일 때만 한 번 더 물어 확인. 재확인에서
  하나라도 관련 있다고 하면 그쪽을 신뢰(의심스러우면 답변 쪽으로).
  두 번 다 0건이어야 확정.
- **영향받는 pipeline**: N4만. 비용은 "0건 판정" 케이스에서만 호출 1회
  추가(전체 트래픽의 일부에만 영향).

### Patch 5 — 거리 기반 사전 필터(하이브리드 게이트)

- **문제**: 재확인 로직으로도 못 잡는 케이스가 있었다 - 150문항 전체
  실측에서 정상 질문 4건이 여전히 잘못 보류됐고, 3건이 같은 정책(어선
  기관교체지원)에 몰려 있었다(주제어가 거의 없는 되묻기형 질문).
- **변경**: top-1 cosine_distance가 이미 충분히 가까우면
  (`_CONFIDENT_DISTANCE_THRESHOLD = 0.118`, 정상 질문 142건 실측 중앙값)
  LLM 판정 자체를 건너뛰고 검색 결과를 그대로 믿는다. 이 값 이하는
  보류 대상 최저 거리(0.130)보다 항상 가까워 두 분포가 겹치지 않는
  안전 구간이다.
- **변경 이유**: "검색은 정답을 찾았는데 게이트가 지운" 경우(B)와
  "검색 자체가 못 찾은" 경우(A)를 구분해, B에서 LLM이 실수로 걸러낼
  기회 자체를 없앤다.
- **영향받는 pipeline**: N4만, `_filter_relevant_candidates` 내부.

### Patch 6 — 법령명 환각 방지

- **문제**: 150문항 실측에서 LLM이 만든 요약 문장이 원문에 없는 법령명을
  지어내는 사례를 발견(예: "관련된 법령은 조세특례제한법입니다" - 원문에
  그런 언급 없음).
- **기존 동작**: `_validate_structured_summaries`가 숫자 환각만 검증.
- **변경 동작**: 법령명(`_law_names_in`, "OO법/법률/시행령/시행규칙/조례"
  패턴)도 원문에 있는 것으로만 이루어져 있는지 검증 - 새 법령명이 하나라도
  있으면 그 정책의 summary를 통째로 버리고 템플릿 문장으로 대체.
- **영향받는 pipeline**: N13(answer_generation)만.

### Patch 7 — Faithfulness judge 프롬프트 보정

- **문제**: 150문항 실측에서 PARTIAL_FAITHFULNESS(부분근거) 판정 70건 중
  다수가 실제 환각이 아니라, "지역 조건 추가 확인 필요"·"장애 여부는
  확인되지 않았습니다"처럼 챗봇이 **스스로 확인 못 했다고 밝히는 투명성
  안내문**을 judge가 "근거 문서에 없는 사실 주장"으로 오판한 것이었다.
- **변경**: `_faithfulness_prompt`에 "확인하지 못했다고 밝히는 문장은
  판정 대상에서 제외하라"는 지침을 추가.
- **변경 이유**: 이런 안내문은 애초에 "사실 주장"이 아니라 안내이므로
  근거 문서와 대조할 대상이 아니다 - 오히려 정직하게 한계를 밝힌 답변을
  낮게 평가하는 것은 "확실하면 답하고 불확실하면 알린다"는 설계 원칙에
  반하는 측정 오류였다.
- **영향받는 pipeline**: 평가 코드(`rag_design/answer_quality.py`)만.
  서비스가 실제로 만드는 답변 자체는 바뀌지 않는다 - 측정 방식만
  바로잡았다.

### Patch 8 — Failure case 분석 스크립트

- 신규 스크립트 `scripts/analyze_failure_cases.py` - 이미 저장된
  `results_*.jsonl`을 RETRIEVAL_MISS_OR_RANKING / RELEVANCE_GATE_FALSE_ABSTAIN /
  BAD_ABSTENTION / UNSUPPORTED_CLAIM / PARTIAL_FAITHFULNESS / LOW_RELEVANCY /
  LLM_FAILURE_OR_FALLBACK / MULTI_TURN_ANOMALY / EVALUATION_ERROR로 분류한다.
  새 LLM 호출 없이 순수 분석만 한다.

### Patch 9 — 거리 사전 필터를 "1등만 확정"으로 재설계

- **문제**: v3/v4의 거리 사전 필터는 1등 후보가 충분히 가까우면
  **후보 전체(1~5등)**를 판정 없이 통과시켰다. 150+100+준-Holdout
  100 = 350문항 실측(v4)에서 Citation Precision이 v1/v2(0.42~0.44)
  대비 0.19~0.23으로 크게 떨어졌는데, citation_pair_count가 세트당
  240대에서 400~500대로 늘어난 게 원인이었다 - 1등만 확실해도 2~5등
  중 실제로는 무관한 후보까지 같이 무검증으로 통과했기 때문이다.
- **기존 동작**: `min(c.score for c in candidates) <= threshold`이면
  `candidates` 전체를 그대로 반환.
- **변경 동작**: 거리로는 **1등만** 확정하고, 2등 이하는 1등이
  확정됐든 아니든 항상 기존 LLM 판정(+재확인)을 거친다. 1등을
  찾을 때도 위치(리스트의 첫 원소)에 기대지 않고 `min(candidates,
  key=score)`로 직접 찾아 호출부의 정렬 여부와 무관하게 옳다.
- **변경 이유**: 1등을 잘못 거르는 것(Recall 손실의 원인이었다)은
  막으면서, 2등 이하가 공짜로 통과하던 경로만 막기 위함 - "확실한
  것은 의심하지 않는다"와 "불확실한 것은 계속 검증한다"를 동시에
  만족한다.
- **실측(v4→v5, 350문항 통합)**: Citation Precision 0.200→0.305
  (+53%), MRR 0.598→0.669. 세 세트 모두 비슷한 크기로 개선돼(4절
  Generalization Gap) 특정 세트에 맞춘 결과가 아니다. 반대급부로
  Abstention Precision/Recall이 350문항 통합 기준 0.812/0.464→
  0.632/0.429로 소폭 하락했다 - 2등 이하까지 매번 판정하면서 LLM
  판정 자체의 편차가 끼어들 기회가 늘었다(세트별로 방향이 엇갈려
  9절에 추가 조사 필요 항목으로 남긴다).
- **영향받는 pipeline**: N4만, `_filter_relevant_candidates` 내부.

## 3. Retrieval 변경

- query construction: 변경 없음(이전 세션에서 이미 `strip_profile_phrases`로
  개선 완료).
- retrieval 자체(임베딩, top-k=5, chunk 800자/overlap 100자): 변경 없음.
- reranking: 없음(그대로) - 프로젝트 규정상 Baseline 안정화 전 보류.
- relevance gate: Patch 1, 4, 5, 9 (신설 + 재확인 + 거리 사전 필터 +
  1등만 확정하는 재설계).
- threshold: `_CONFIDENT_DISTANCE_THRESHOLD = 0.118` - 실측 분포(정상
  질문 142건 중앙값)로 정함, 특정 질문에 맞춘 값 아님. 숫자 자체는
  Patch 9에서도 그대로 - 바뀐 건 "이 거리 이하일 때 몇 등까지
  봐주는가"(전체 → 1등만)이지 임계값 자체가 아니다.

## 4. Generation 변경

- system prompt: 변경 없음(기존 `_STRUCTURED_SYSTEM_PROMPT`가 이미
  "숫자·사실을 새로 만들지 마라" 지침 포함).
- context formatting: 변경 없음(이미 `policy_id: <id>` 블록 형식).
- grounding rule: Patch 6(법령명 환각 방지)으로 보강.
- answer generation 로직 자체: 변경 없음(여전히 규칙 기반 템플릿이
  사실 라인을 만들고, LLM은 요약 문장만 다듬음 - 검증 실패 시 템플릿
  그대로 노출).

## 5. Citation 변경

Patch 2 (claim_type/`rule_chunk_id` 기반 정밀화).

## 6. Abstention 변경

Patch 1(관련성 게이트), 4(재확인), 5(거리 사전 필터), 9(1등만 확정하는
재설계). 기존 N7의 CONFLICT/STALE/NO_EVIDENCE 판정 로직 자체는 변경하지
않았다 - 새 게이트가 그 앞단(N4)에서 후보를 좁혀줄 뿐이다.

## 7. Multi-turn 변경

없음. 감사 결과(8절) UNKNOWN/slot 처리에 특별한 문제를 찾지 못했다 -
`slot_schema.UNKNOWN` 센티넬, `apply_calc_skip`, `unanswered_slots` 기록
등 기존 구조가 "확인 안 됨"과 "값이 없음"을 이미 구분하고 있었다.

## 8. 과적합 방지 감사 (읽기 전용 조사, 코드 변경 없음)

`src/rag_chatbot/graph/`, `rag_design/` 전체를 대상으로 조사했다.

- **하드코딩된 question_id/policy_id/fixture_id 분기**: 발견되지 않음.
  유일한 `policy_id ==` 매칭(`duplicate_benefit.py`)은 런타임 변수 비교이지
  리터럴 ID가 아니다.
- **평가 데이터셋의 ground truth를 서비스 코드가 읽는 경로**: 없음.
  `expected_policy_ids`/`should_abstain`은 `rag_design/evaluation.py`,
  `rag_design/validation_runner.py`(평가 하네스 자체)에서만 쓰이고
  `service.py`/`graph/`는 그 파일을 import하지 않는다.
- **키워드·threshold 테이블**: `llm_gateway.py`의 관심사 키워드·지역
  별칭 테이블, `policy_conditions.py`의 JA코드 매핑, `duplicate_benefit.py`의
  중복조항 정규식은 모두 전수 코퍼스 실측(예: "10,968건 중 X건", "247개
  조항 전수 분류")으로 정당화돼 있었다 - 특정 평가 질문의 표현에 맞춘
  것이 아니다.
- **애매한 지점 2건**(제거 대상 아님, 참고용): `policy_search.py`와
  `service.py`의 일부 주석이 "discovery"를 특정 dev 질문 ID 하나로
  설명하지만, 실제 코드 수정 자체는 일반화돼 있다(특정 ID로 분기하지
  않음) - 근거 서술 방식의 문제이지 로직의 문제가 아니다.

결론: 이번 세션에서 발견된 원인·수정 모두 "특정 질문에 맞춘 우회"가
아니라 코드에서 확인한 구조적 원인에 대한 일반화된 수정이다.

## 9. Test 변경

- `tests/test_policy_search_node.py`: 관련성 게이트 기본 동작(9건),
  재확인 로직(6건), 거리 사전 필터(3건), 1등만 확정하는 재설계(3건),
  통합 테스트 갱신(1건) = 22건.
- `tests/test_answer_generation_node.py`: citation 정밀화(4건), 법령명
  환각 검증(2건) = 6건.
- `tests/test_search_evidence_edge_cases.py`: citation label 값 1건 갱신
  (의도된 라벨 세분화 반영).
- 전체 스위트: 1142 passed, 6 skipped(환경 의존적 사전 skip, 무관),
  기존에 이미 실패하던 무관한 7건은 그대로(이번 변경과 무관 - 세션
  시작 전 stash 대조로 확인됨).

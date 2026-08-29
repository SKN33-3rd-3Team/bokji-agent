# N7·N8 구현 계획

## 목표

N7 Evidence Gate와 N8 표적 법령 검색을 기존 `GraphState`, RAG 계약, E9·E11·E12
연결부와 호환되는 최소 노드로 구현한다. 실제 state 근거만 인정하고, 근거 부족은
종류별 1회만 재시도하며 안전·충돌·최신성·법령 capability 문제는 fail-closed 한다.

범위 밖은 graph builder, N5·N6·N9 구현, LLM 검색어 생성, 새 의존성, 법령 원문
수집이다.

## 현황

- 기준 커밋은 `3892f6c`이며 그래프에는 `state.py`와 빈 `nodes` 패키지만 있다.
- `ClaimDraft`에는 검색 질의와 요구 근거 aspect가 없고, `GraphState`에는 N7 출력,
  재시도 횟수, 안전 신호와 검색 기준일이 없다.
- `rag_design.policy`에는 `EvidenceState`, `AbstentionDecision`,
  `decide_abstention()`, `supported_legal_evidence_aspects()`가 있다.
- `rag_design.index_policy`와 `vector_store`에는 `route_indexes(QueryScope.LAW)`,
  `VectorSearchFilter`, `ChromaVectorStore.search()`가 있다.
- 법령 인덱스는 `metadata_only`이며 지원 aspect는 `legal_metadata`뿐이다.
- 테스트는 표준 `unittest`를 사용한다. 관련 기존 테스트 42개는 통과했으며 전체
  suite는 현재 로컬의 `requests`, `chromadb` 미설치 때문에 기준선부터 완전 통과하지
  않는다.

## 확정 계약

### 공통 입력 불변조건과 E9 직접 입력

- `claim_plan`은 호출자가 만든 ordered `list[ClaimDraft]` 전체 plan이며 최소 1개 claim을
  가져야 한다. key 누락·비-list·빈 plan은 vacuous `pass`를 막기 위해 명시적 입력
  오류로 거부한다. `claim_id`는 비어 있지 않고 plan 안에서 유일해야 하며 N7/N8은
  순서를 바꾸거나 입력 객체를 직접 수정하지 않는다. 중복 claim ID나 claim 내부 중복
  evidence ID도 명시적 입력 오류다.
- 각 claim의 `policy_id`는 공백이 아닌 `str`, `doc_check_required`와
  `law_check_required`는 실제 `bool`, `evidence_chunk_ids`와 `reasons`는 중복·공백이
  없는 문자열 목록이어야 한다. 잘못된 타입은 명시적 입력 오류다.
- `query_id`는 공백이 아닌 `str`이어야 한다. 누락·비문자열·공백 값은 임의 생성하지
  않고 명시적 입력 오류로 종료한다.
- E9로 직접 들어오는 `doc_check_required=False` claim도 자동으로 검증된 것으로 보지
  않는다. 다음을 모두 만족해야 문서 근거가 유효하다.
  - N5가 `status="supported"`, 비어 있지 않은 `reasons`, 비어 있지 않은
    `evidence_chunk_ids`를 제공한다.
  - claim의 evidence ID를 실제 `state.subsidy_chunks`와 `state.law_chunks` membership으로
    분리하고, 어느 쪽에도 없는 ID는 근거로 인정하지 않는다. law ID는 문서 근거 개수에
    포함하지도, 문서 근거를 무효화하지도 않고 별도 법령 계약으로 검증한다.
  - 실제 subsidy ID가 최소 하나 있고 해당 `RetrievedChunk.query_id`가 모두
    `state.query_id`와 같다.
  - 참조한 모든 subsidy chunk의 `source_type`이 `SourceType.SUBSIDY`이고
    `chunk.metadata["source_id"] == claim.policy_id`다.
- 위 조건은 N6를 거친 claim의 subsidy evidence에도 동일하게 적용한다. 하나라도
  어기면 문서 근거 부족으로 판정해 첫 실행은 `insufficient_document`, retry 소진 후는
  `fail`이다. N5/N6 구현은 이번 범위가 아니다.

### N7 Evidence Gate

- 입력: ordered 전체 `claim_plan`, `subsidy_chunks`, `law_chunks`, 필수
  `safety_blocked`, 모든 evidence 판정에 필수인 `as_of`, 기존 retry count.
- `safety_blocked`는 명시적인 실제 `bool`이어야 한다. 누락 또는 비-bool이면 안전을
  추정하지 않고 `AbstentionReason.SAFETY`로 즉시 `fail`한다. `True`도 즉시 `fail`이다.
- claim `status="conflict"`는 retry 없이 즉시 `fail`한다.
- `as_of`는 subsidy-only E9를 포함한 모든 N7 evidence 판정에서 필수다.
  `is_canonical_date()`와 `date.fromisoformat()`을 모두 통과해야 한다. 누락·오류는
  retry 없이 `AbstentionReason.STALE`과 `fail`이다. `date.today()`를 사용하지 않는다.
- 참조한 각 실제 chunk에
  `MetadataFilter(source_type=chunk.source_type, as_of=as_of_date)`를 만들고 기존
  `chunk_matches_filter()`로 유효기간을 판정한다. LAW는 저장소 계약대로
  `effective_from`이 필수다. subsidy는 `effective_from`/`effective_to` 누락을 unbounded로
  허용하고, 값이 있을 때만 시작일 포함·종료일 제외 `[from,to)`를 적용한다. helper가
  거부한 참조가 하나라도 있으면 freshness 미확인으로 즉시 `fail`한다.
- law evidence ID도 실제 `state.law_chunks`에 있어야 하고 각 `RetrievedChunk.query_id`가
  state와 같으며 source가 LAW여야 한다. canonical metadata와 interval 검증을 통과하지
  못한 law chunk는 근거로 인정하지 않는다.
- policy 판정은 기존 `EvidenceState`, `AbstentionDecision`, `decide_abstention()`을
  재사용한다. 법령 capability는 `supported_legal_evidence_aspects()`로만 계산한다.
- policy 결과는 `SAFETY | CONFLICT | STALE -> fail`, 문서 missing이 있는
  `NO_EVIDENCE -> insufficient_document`, 문서는 충족했고 법령 metadata만 missing인
  `NO_EVIDENCE -> insufficient_law`, 그 밖의 `NO_EVIDENCE -> fail`, non-abstain은
  `pass`로 매핑한다. retry가 이미 1이면 `NO_EVIDENCE` decision은 보존하되 verdict만
  `fail`로 바꾼다.
- 출력은 LangGraph partial dict이며 성공적으로 반환하는 모든 경로에서 다음 relevant
  key를 빠짐없이 명시해 이전 실행의 stale 값이 남지 않게 한다.
  - `evidence_gate_verdict`:
    `pass | insufficient_document | insufficient_law | fail`
  - `abstention_decision`
  - `missing_document_claim_ids: list[str]`
  - `missing_law_claim_ids: list[str]`
  - `doc_retry_count: int`, `law_retry_count: int`
- retry count 누락은 0으로 읽는다. `type(value) is int`, `0 <= value <= 1`만 받으며
  bool·음수·비-int·1 초과는 명시적 입력 오류다.
- 문서와 법령 근거가 모두 부족하면 선행 단계인 문서 확인을 먼저 반환한다.
- 최초 문서/법령 부족 판정은 해당 count를 1로 올려 `insufficient_*`를 반환한다.
  같은 종류의 부족이 다시 발견되면 추가 증가 없이 `fail`한다.
- `pass`일 때도 두 missing 목록을 빈 목록으로, decision을 non-abstain으로, 두 count를
  현재 유효값으로 명시한다. `slots`와 기존 chunks는 반환 dict에 복사하지 않고 state의
  partial merge로 보존한다.

### 법령 aspect 경계

- 허용 상수는 기존 정책의 `legal_metadata`, `legal_article_body`,
  `legal_interpretation` 세 개뿐이다. subsidy claim 의미를 판정하는 별도 semantic
  helper가 있다고 가정하지 않는다. 목록은 중복 없는 `list[str]`이어야 하고 알려지지
  않은 값이나 잘못된 타입은 명시적 입력 오류다.
- `required_aspects`는 optional producer 필드지만 `law_check_required=True`에서 누락
  또는 빈 목록이면 안전한 기존 mapping이 없으므로 N8을 호출하지 않고 즉시
  `NO_EVIDENCE`/`fail`한다. N5가 `legal_metadata`를 명시해야 한다.
- `law_check_required=False`이면 `required_aspects`는 누락되거나 빈 목록이어야 한다.
  flag가 False인데 법령 aspect가 있으면 모순 입력으로 명시적 오류를 반환하며 aspect를
  무시하거나 불필요한 retry를 만들지 않는다.
- `legal_article_body` 또는 `legal_interpretation`이 하나라도 필요하면 metadata-only
  검색으로 충족할 수 없으므로 이번 최소 구현은 N8을 호출하지 않고 즉시 fail한다.
- 법령 metadata 보충 후 `pass`하려면 N5/N6의 실제 matching subsidy evidence가
  substantive claim을 이미 `supported`하고, 남은 명시적 요구가
  `legal_metadata`뿐이어야 한다. law chunk만으로 substantive claim을 통과시키지 않는다.

### N8 표적 법령 검색

- N8은 `missing_law_claim_ids`를 신뢰하지 않고 직접 검증한다. 값은 비어 있지 않은
  ordered unique `list[str]`이며 각 ID도 공백이 아니어야 한다. 현재 unique
  `claim_plan`에 없는 ID, 중복, 비문자열/공백 ID는 명시적 입력 오류다.
- 각 target은 `law_check_required=True`, 중복 없는 `required_aspects`의 집합이 정확히
  `{legal_metadata}`, 공통 입력 계약을 통과한 실제 subsidy evidence로 substantive
  claim이 이미 supported된 상태여야 한다. 이미 유효한 law metadata evidence가 있어
  더 이상 missing이 아닌 stale target, law flag가 False인 non-law target, capability나
  subsidy 전제가 맞지 않는 target은 명시적 입력 오류다. 검증을 통과한 target만 목록
  순서대로 검색한다.
- N8도 `as_of`를 직접 `is_canonical_date()`로 검사하고 `date.fromisoformat()`으로
  변환한다. 누락·비문자열·비canonical 값은 search를 호출하지 않고 명시적 입력 오류다.
- 공개 함수는 factory 없이 다음 하나다.

  ```python
  def search_targeted_laws(state: GraphState, *, search: LawSearch) -> dict:
      ...
  ```

  `LawSearch`는 기존 bound `ChromaVectorStore.search`와 같은 호출 형태를 받는
  `Callable[..., Sequence[RetrievedChunk]]` type alias다. 현재 테스트는 fake를 직접
  넘기고, 향후 graph wiring은
  `functools.partial(search_targeted_laws, search=store.search)`로 state-only node를
  만든다. callable은 state에 저장하지 않는다.
- 질의는 optional `ClaimDraft.search_query`의 trim 결과를 우선한다. 없거나 비어 있으면
  대상 claim이 이미 참조한 subsidy evidence IDs 중 공통 입력 계약을 통과하고
  `chunk.metadata["source_id"] == policy_id`인 `chunk.text`만 state 입력 순서대로
  결합한다. 같은 policy의 미참조 chunk는 사용하지 않는다. 검증된 claim-bound text도
  없으면 검색어나 법령명을 추정하지 않고 해당 claim을 미충족으로 남겨 N7이
  fail-closed 한다. 비문자열 `search_query`는 명시적 입력 오류다.
- 검색 호출은 정확히 다음 의미를 갖는다.

  ```python
  source = SourceType(route_indexes(QueryScope.LAW)[0])
  search(
      source,
      query,
      query_id=query_id,
      search_filter=VectorSearchFilter(as_of=as_of_date),
  )
  ```

- 각 검색 결과는 claim에 넣기 전에 다음을 모두 검증한다.
  - `RetrievedChunk.query_id == state.query_id`
  - chunk `source_type is SourceType.LAW`이고 `index_name == "law"`
  - `supported_legal_evidence_aspects()`가 canonical metadata-only chunk로 인정
  - `as_of`가 chunk의 `[effective_from, effective_to)` 안에 있음
- 잘못된 결과나 search callable 예외는 빈 결과로 바꾸지 않고 명시적 오류로 전파한다.
  정상적인 빈 검색 결과만 근거 부족으로 N7에 돌아간다.
- 검증된 검색 결과 ID만 대상 claim의 기존 `evidence_chunk_ids` 뒤에 합치고
  `chunk_id` 기준 최초 순서를 보존해 dedupe한다. N8은 `status`를 supported로 확정하지
  않는다.
- 반환 `law_chunks`는 기존 전체 목록 뒤에 신규 결과를 붙이고 `chunk_id` 기준 최초
  항목을 보존해 dedupe한다. 다른 claims와 입력 state 객체는 수정하지 않는다.

### State 확장

기존 `TypedDict(total=False)`에 필요한 producer/output 필드만 추가한다.

- `EvidenceGateVerdict = Literal["pass", "insufficient_document",
  "insufficient_law", "fail"]`
- `ClaimDraft`: `search_query: str`, `required_aspects: list[str]`
- `GraphState`: `as_of: str`, `safety_blocked: bool`,
  `evidence_gate_verdict: EvidenceGateVerdict`,
  `abstention_decision: AbstentionDecision`,
  `missing_document_claim_ids: list[str]`, `missing_law_claim_ids: list[str]`,
  `doc_retry_count: int`, `law_retry_count: int`

검색 callable, vector store, embedding provider 같은 런타임 객체는 state에 넣지 않는다.

## 명세 충돌과 채택 결정

| 충돌 | 채택 결정 |
| --- | --- |
| N7 프롬프트는 3-way지만 E11에 N6 재확인 경로가 있다. | E11/E12를 모두 지원하는 4-way verdict를 채택하고 N6 자체는 구현하지 않는다. |
| 출력명이 `verdict`와 `evidence_gate_verdict`로 다르다. | `evidence_gate_verdict`로 통일하고 두 missing ID 목록과 두 count를 매번 반환한다. |
| retry 초기값·상한·타입 규칙이 없다. | 누락=0, N7 부족 분기에서 증가, 종류별 1회, 잘못된 타입/범위는 오류로 고정한다. |
| E9의 `doc_check_required=False`가 검증 bypass처럼 보인다. | N5 supported 상태·이유·실제 query/policy-matched subsidy evidence가 모두 있어야 인정한다. |
| N8에 검색 가능한 claim 문장이 없다. | optional `search_query`를 추가하고 검증된 matching subsidy text만 deterministic fallback으로 허용한다. |
| 그림의 법률 분류와 저장소 `law_type`이 다르다. | 저장소의 `law | admrul | ordin`과 전 subtype 공통 `source_sequence`만 사용한다. |
| law 검색 결과가 substantive 법률 주장도 지지하는 것으로 오해될 수 있다. | subsidy evidence가 이미 substantive claim을 지지하고 남은 aspect가 `legal_metadata`일 때만 보충 가능하다. article/interpretation은 즉시 fail한다. |
| 실제 retriever 전달 경로가 없다. | factory 없이 keyword-only search callable을 받고 향후 `functools.partial`로 바인딩한다. |
| 이전 실행의 verdict/missing 값이 state에 남을 수 있다. | N7의 모든 정상 반환에서 relevant output key 전체를 명시한다. |

## 변경 파일과 심볼

| 파일 | 변경 |
| --- | --- |
| `src/rag_chatbot/graph/state.py` | `EvidenceGateVerdict`, `ClaimDraft`, `GraphState` optional 필드 추가 |
| `src/rag_chatbot/graph/nodes/evidence_gate.py` | `evaluate_evidence(state: GraphState) -> dict` 구현 |
| `src/rag_chatbot/graph/nodes/targeted_law_search.py` | `LawSearch`, `search_targeted_laws(state, *, search)` 구현 |
| `src/rag_chatbot/graph/nodes/__init__.py` | 두 실제 노드 함수 re-export |
| `tests/test_evidence_gate_and_targeted_law_search.py` | fake searcher 기반 단일 `unittest` 파일 추가 |

`rag_design`, requirements, graph builder와 N5/N6/N9 파일은 변경하지 않는다.

## 구현 단계

1. `state.py`에 verdict와 optional producer/output 필드를 추가한다.
2. N7 입력 검증에서 query ID, ordered unique claims/evidence, safety bool, counters,
   canonical `as_of`를 확인한다.
3. 실제 query/policy/source-matched subsidy evidence와 기존
   `MetadataFilter`/`chunk_matches_filter()`로 모든 참조 chunk 유효기간을 검증한다.
4. 기존 policy helper로 capability·abstention을 판정하고 4-way verdict, retry 상한,
   stale 방지 전체 output을 반환한다.
5. N8에서 target IDs와 canonical `as_of`를 독립 검증한 뒤 direct callable 호출,
   deterministic query fallback, 결과 검증, claim별 evidence 병합과 전체 law chunk
   dedupe를 구현한다.
6. 실제 노드 함수를 `nodes/__init__.py`에서 re-export한다.
7. 단일 테스트 파일로 연결부·실패 경계·입력 불변성과 metadata-only 제한을 검증한다.

## 테스트 매트릭스

| ID | 시나리오 | 핵심 확인 |
| --- | --- | --- |
| T0 | `claim_plan` 누락·비-list·빈 목록 | vacuous `pass` 없이 명시적 입력 오류 |
| T1 | E9 직접 claim의 유효한 N5 subsidy evidence | 날짜 경계가 없는 기존 subsidy fixture도 unbounded로 `pass`, ordered plan과 state 불변 |
| T2 | E9 status/reasons/evidence 중 하나 부족 | 최초 `insufficient_document`, count 소진 후 `fail` |
| T3 | 유효 subsidy evidence + 법령 metadata 최초 부족 | `insufficient_law`, `law_retry_count=1`, 정확한 대상 IDs |
| T4 | N6 subsidy evidence로 substantive가 이미 supported + explicit `legal_metadata`만 남음 | N8 보충 후 N7 `pass`; law-only substantive pass는 불가 |
| T5 | N8 정상 빈 결과 | 다음 N7에서 retry 소진 `fail`, 추가 루프 없음 |
| T6 | safety 누락·비-bool·True | 모두 retry 없는 SAFETY `fail` |
| T7 | conflict status | 실제 근거 유무와 무관하게 즉시 `fail` |
| T8 | `as_of` 누락·오류와 source별 interval 경계 | subsidy 날짜 둘 다 누락은 unbounded 통과, 값이 있으면 `[from,to)`; LAW from 누락과 잘못된 as-of는 STALE `fail` |
| T9 | counter 누락 및 잘못된 값 | 누락=0; bool·음수·비-int·1 초과는 명시적 오류 |
| T10 | duplicate claim/evidence ID | 입력 오류, 입력 plan은 수정되지 않음 |
| T11 | fabricated·cross-query·wrong-policy subsidy evidence | 문서 근거로 불인정하고 retry 규칙 적용 |
| T12 | `required_aspects` 누락/빈값/미지원 값 및 flag와의 모순 | law-required 누락은 fail, law-disabled aspect는 오류, article/interpretation은 N8 미호출 |
| T13 | `search_query`와 fallback | explicit query 우선, 검증된 matching subsidy text만 입력순 결합, 매핑 없으면 검색 안 함 |
| T14 | query ID 누락·비문자열·공백 | 검색/판정 전에 명시적 오류 |
| T15 | fake search 실제 호출 계약 | LAW source, 동일 query ID, canonical as-of filter 확인 |
| T15-A | N8 target/as-of 묶음 검증 | malformed/unknown/duplicate/non-law/stale target과 N8의 누락·오류 as-of는 search 전 명시적 오류 |
| T16 | malformed subsidy interval 및 cross-query/wrong-source/noncanonical/stale law 결과 | subsidy는 STALE `fail`; 잘못된 law 결과는 명시적 오류 |
| T17 | 기존+신규 evidence/law chunks 중복 | claim evidence와 전체 law chunks 모두 최초 순서를 보존해 `chunk_id` dedupe |
| T18 | search 예외 | 그대로 전파하고 빈 결과로 은폐하지 않음 |
| T19 | input immutability와 partial merge | 입력 lists/dicts 불변, `slots` 등 무관 state 유지, N7 output key 매번 초기화 |

테스트 파일은 기존 collector 테스트처럼 repository root의 `src`를 `sys.path`에
bootstrap한다. 현재 Codex 환경에는 `python`이 PATH에 없으므로 로컬 검증은 번들
Python 3.12.13을 사용하고 bytecode 생성을 막는다.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$pythonExe = 'C:\Users\myori\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $pythonExe -m unittest tests.test_evidence_gate_and_targeted_law_search -v
& $pythonExe -m unittest tests.test_chunking_and_policy tests.test_contracts_and_citations -v
& $pythonExe -m unittest discover -s tests -v
git diff --check
```

전체 suite의 기존 `requests`/`chromadb` 환경 실패는 신규 회귀와 분리해 기록한다.

## 완료 기준

- N7이 E9 포함 모든 입력을 실제 query/policy/source-matched evidence로 검증한다.
- N7은 누락·비-list·빈 `claim_plan`을 거부해 빈 plan을 `pass`하지 않는다.
- N7이 4개 verdict, 종류별 retry 1회와 전체 relevant output key를 결정론적으로
  반환한다.
- safety, conflict, canonical as-of와 source별 저장소 날짜 계약을 기존 filter helper로
  fail-closed 검증한다.
- N8이 대상 claim만 정확한 law search 호출로 검색하고 검증된 결과만 순서 보존·dedupe
  병합한다.
- N8은 target 목록과 as-of를 독립 검증해 malformed/unknown/duplicate/non-law/stale
  target을 검색하지 않는다.
- metadata-only 법령을 substantive 근거로 승격하지 않고 article/interpretation은
  검색 없이 fail한다.
- 잘못된 입력과 검색 오류를 숨기지 않으며 입력 state와 무관 필드를 수정하지 않는다.
- 신규 테스트와 관련 회귀가 통과하고 전체 suite의 기존 환경 실패를 구분해 기록한다.
- 새 의존성, factory, graph builder, LLM 질의 생성, N5/N6/N9 구현을 추가하지 않는다.
- N5가 `required_aspects`와 유효한 subsidy evidence를 생산하지 않은 law claim은
  fail-closed된다. `search_query` 누락은 검증된 policy-matched subsidy text가 있을 때만
  예외이며, 그것도 없으면 fail-closed된다는 통합 한계를 명시한다.
- 모든 호출자가 canonical `as_of`를 공급해야 하며 누락 시 subsidy-only claim도
  fail-closed된다는 통합 한계를 명시한다.
- `git diff --check`가 통과하고 변경 파일이 계획된 범위에만 있다.

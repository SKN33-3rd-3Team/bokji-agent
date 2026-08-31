# N7·N8 구현 계획

## 목표

N7 Evidence Gate와 N8 표적 법령 검색을 기존 `GraphState`, RAG 계약, E9·E11·E12
연결부와 호환되는 최소 노드로 구현한다. N5/N6가 구조화해 선언한 공식 법령 원천과
실제 state 근거만 인정하고, 근거 부족은 종류별 1회만 재시도하며 provenance·안전·
충돌·최신성·법령 capability 문제는 fail-closed 한다.

범위 밖은 graph builder, N5·N6·N9 구현, LLM 검색어 생성, 새 의존성, 법령 원문
수집이다.

## 현황

- 기준 커밋은 `3892f6c`이며 그래프에는 `state.py`와 빈 `nodes` 패키지만 있다.
- `ClaimDraft`에는 검색 질의, 요구 근거 aspect와 공식 법령 원천 선언이 없고,
  `GraphState`에는 N7 출력, 재시도 횟수, 안전 신호와 검색 기준일이 없다.
- `rag_design.policy`에는 `EvidenceState`, `AbstentionDecision`,
  `decide_abstention()`, `supported_legal_evidence_aspects()`가 있다.
- `rag_design.index_policy`와 `vector_store`에는 `route_indexes(QueryScope.LAW)`,
  `VectorSearchFilter`, `ChromaVectorStore.search()`가 있다.
- `rag_design.chunking`에는 canonical 법령 text renderer와 `compute_chunk_id(Document,
  ...)`가 있고, `rag_design.citation`에는 subtype별 공식 공개 URL을 만드는
  `legal_citation_url()`이 있다. N7은 parent `Document`가 없으므로 기존 chunk ID 공식을
  재구현하지 않고 doc ID 기반 최소 helper를 같은 모듈에서 추출해 공유해야 한다.
- 법령 인덱스는 `metadata_only`이며 지원 aspect는 `legal_metadata`뿐이다.
- 테스트는 표준 `unittest`를 사용한다. 관련 기존 테스트 42개는 통과했으며 전체
  suite는 현재 로컬의 `requests`, `chromadb` 미설치 때문에 기준선부터 완전 통과하지
  않는다.

## 확정 계약

### 공통 입력 불변조건과 E9 직접 입력

- safety fail short-circuit 뒤의 safe 입력에서 `claim_plan`은 호출자가 만든 ordered
  `list[ClaimDraft]` 전체 plan이며 최소 1개 claim을 가져야 한다. key 누락·비-list·빈
  plan은 vacuous `pass`를 막기 위해 명시적 입력 오류로 거부한다. `claim_id`는 비어
  있지 않고 plan 안에서 유일해야 하며 N7/N8은
  순서를 바꾸거나 입력 객체를 직접 수정하지 않는다. 중복 claim ID나 claim 내부 중복
  evidence ID도 명시적 입력 오류다.
- 각 claim의 `policy_id`는 안정적인 원천 provenance인
  `Chunk.metadata["source_id"]`와 같은 공백이 아닌 `str`이어야 한다. 부모 subsidy
  문서의 버전 포함 `Chunk.doc_id`는 별도로 canonical 일관성을 검증한다.
  따라서 같은 `source_id`의 다른 버전도 선언 evidence ID가 가리킨 parent ID가
  canonical하고 기존 `as_of` filter를 통과하면 허용한다.
  `doc_check_required`와
  `law_check_required`는 실제 `bool`, `evidence_chunk_ids`와 `reasons`는 중복·공백이
  없는 문자열 목록이어야 한다. 잘못된 타입은 명시적 입력 오류다.
- `claim_type`은 필수이며 정확히 `eligibility | amount | duplicate` 중 하나다. 누락,
  다른 문자열, 비문자열 값은 명시적 입력 오류다.
- safe 입력의 `query_id`는 공백이 아닌 `str`이어야 한다. 누락·비문자열·공백 값은
  임의 생성하지 않고 명시적 입력 오류로 종료한다.
- E9로 직접 들어오는 `doc_check_required=False` claim도 자동으로 검증된 것으로 보지
  않는다. 다음을 모두 만족해야 문서 근거가 유효하다.
  - N5가 `status="supported"`, 비어 있지 않은 `reasons`, 비어 있지 않은
    `evidence_chunk_ids`를 제공한다.
  - claim이 선언한 각 evidence ID를 실제 `state.subsidy_chunks`와
    `state.law_chunks`의 합집합에서 정확히 한 번만 resolve한다. unknown ID, 같은 ID의
    중복 결과, 두 pool에 동시에 존재하는 ID는 단순 근거 부족이 아니라 retry 불가
    provenance 오류다. law ID는 문서 근거 개수에 포함하지 않고 별도 법령 계약으로
    검증한다.
  - 실제 subsidy ID가 최소 하나 있고 해당 `RetrievedChunk.query_id`가 모두
    `state.query_id`와 같다.
  - 참조한 모든 subsidy chunk의 `source_type`이 `SourceType.SUBSIDY`이고
    `chunk.metadata["source_id"] == claim.policy_id`다. metadata의 `source_id`와
    선택된 version field로 재계산한 canonical parent ID도 `chunk.doc_id`와 같아야 한다.
- 위 조건은 N6를 거친 claim의 subsidy evidence에도 동일하게 적용한다. status·reasons·
  evidence 목록 자체가 부족한 clean claim은 첫 실행 `insufficient_document`, retry
  소진 후 `fail`이다. 반면 선언 ID의 unknown/dual resolve, cross-query, wrong-source,
  wrong-policy, wrong-parent-doc-id나 parent/metadata 불일치는 재검색으로 고칠 수 없는
  입력 무결성 오류이므로 즉시 terminal `fail`하고
  두 missing 목록을 비운다. N5/N6 구현은 이번 범위가 아니다.

### 공식 법령 원천 선언

- state 계약에 다음 total `TypedDict`를 추가한다. 두 key는 모두 필수다.

  ```python
  class LawSourceRef(TypedDict):
      law_type: Literal["law", "admrul", "ordin"]
      source_id: str
  ```

- `ClaimDraft.required_law_sources`는 optional `list[LawSourceRef]`다. 각 `source_id`는
  비어 있지 않은 ASCII decimal 문자열이어야 하며 정수로 바꾸지 않아 선행 0을
  보존한다. identity는 `(law_type, source_id)`이고 입력 순서를 보존하며 pair 중복은
  명시적 입력 오류다. 목록은 all-of라서 선언된 모든 pair가 충족되어야 한다.
- `law_check_required=True`이고 `required_aspects == ["legal_metadata"]`인 검색 가능
  claim은 `required_law_sources`가 비어 있지 않아야 한다. `law_check_required=False`이면
  이 필드는 누락되거나 빈 목록이어야 하며 nonempty 값은 모순 입력 오류다.
  article/interpretation이 포함된 claim은
  N8 검색 대상이 아니지만, metadata 지원 여부를 계산하려고 목록을 제공했다면 같은
  shape·identity 검증을 적용한다. 검색 가능 claim의 목록이 누락·빈 값이면 identity를
  추정하지 않고 terminal `NO_EVIDENCE`/`fail`하며 두 missing 목록을 비우고 N8을
  호출하지 않는다.
- 이 목록은 N5/N6가 공식 문서의 구조화 metadata에서 생산하는 producer 계약이다.
  N8은 검색 top hit의 identity를 보고 원천을 역선택하거나 source ID를 추정하지 않는다.

### N7 Evidence Gate

- 입력: ordered 전체 `claim_plan`, `subsidy_chunks`, `law_chunks`, 필수
  `safety_blocked`, 모든 evidence 판정에 필수인 `as_of`, 기존 retry count.
- `safety_blocked`는 명시적인 실제 `bool`이어야 한다. 누락 또는 비-bool이면 안전을
  추정하지 않고 `AbstentionReason.SAFETY`로 즉시 `fail`한다. `True`도 즉시 `fail`이다.
- claim `status="conflict"`는 retry 없이 즉시 `fail`한다.
- `as_of`는 subsidy-only E9를 포함한 모든 N7 evidence 판정에서 필수다.
  `is_canonical_date()`와 `date.fromisoformat()`을 모두 통과해야 한다. 누락·오류는
  retry 없이 `AbstentionReason.STALE`과 `fail`이다. `date.today()`를 사용하지 않는다.
- 위 공통 검증 뒤에는 declared ID exact resolution과 provenance를 먼저 확정하고,
  통과한 실제 chunk에만 strict date/freshness와 coverage 판정을 적용한다.
- 참조한 각 실제 chunk에
  `MetadataFilter(source_type=chunk.source_type, as_of=as_of_date)`를 만들고 기존
  `chunk_matches_filter()`로 유효기간을 판정한다. LAW는 저장소 계약대로
  `effective_from`이 필수다. subsidy는 `effective_from`/`effective_to` 누락을 unbounded로
  허용하고, 값이 있을 때만 시작일 포함·종료일 제외 `[from,to)`를 적용한다. helper가
  거부한 참조가 하나라도 있으면 freshness 미확인으로 즉시 `fail`한다.
- 선언된 evidence ID는 두 chunk pool의 `chunk_id` index에서 정확히 한 항목으로
  resolve해야 한다. resolve된 항목의 pool, `chunk.source_type`, `index_name`이 서로
  일치하고 `RetrievedChunk.query_id == state.query_id`여야 한다. unknown·duplicate·dual,
  cross-query, wrong-source, subsidy wrong-policy, wrong-parent-doc-id 또는
  parent/metadata 불일치, claim이 선언하지 않은 law identity는
  retry 불가 terminal `NO_EVIDENCE`/`fail`이며 missing 목록은 둘 다 빈 목록이다.
- law evidence의 identity는 실제 chunk metadata의 `(law_type, source_id)`로 계산하고
  claim의 `required_law_sources`에 정확히 있어야 한다. 예상하지 않은 pair는 canonical
  chunk라도 terminal `fail`이다. 실제 law `source_id`와 `source_sequence`도 nonempty
  ASCII decimal 문자열로 검증하며 정수 변환하지 않는다. 한 expected pair에서 같은
  `as_of`에 유효한 서로 다른 `source_sequence`가 동시에 참조되면 어느 버전도 임의
  선택하지 않고 `CONFLICT`/`fail`한다. 같은 source sequence의 여러 chunk는 아래의
  revision·part 불변조건을 모두 만족한 canonical part일 때만 함께 허용한다.
- 각 law chunk는 `LegalDocumentType`, `compute_document_id()`,
  `compute_content_hash()`, `supported_legal_evidence_aspects()`,
  `is_canonical_date()`와 `chunk_matches_filter()` 등 기존 계약을 조합해 검증한다.
  `effective_from`, metadata `effective_date`, `issued_date`는 canonical date 필수이고
  optional `effective_to`, `source_updated_at`도 값이 있으면 canonical이어야 한다.
  `effective_date == effective_from`, `effective_to > effective_from`, `as_of`가
  `[effective_from, effective_to)` 안에 있어야 한다. `Chunk`/`RetrievedChunk` 생성자가
  이미 검사하는 schema·index 불변조건과 기존 canonical renderer를 재사용하고,
  parent `Document`가 없는 N7에서 `validate_chunk_batch()`를 흉내 낸 중복 validator를
  새로 만들지 않는다. N7에서 strict LAW date/interval 위반은 `STALE`/`fail`, hash·
  computed document identity·canonical metadata 위반은 terminal `NO_EVIDENCE`/`fail`로
  고정한다. N8 검색 결과의 같은 위반은 adapter 계약 오류로 전파하고 merge하지 않는다.
- 각 current law hit는 위 검증에 더해 다음 parentless deterministic 계약을 모두
  만족해야 한다.
  - `chunk_part`와 `chunk_part_count`는 bool이 아닌 int이고
    `0 <= chunk_part < chunk_part_count`, `chunk_part_count > 0`이며
    `chunk.ordinal == chunk_part`다.
  - `chunking_config_from_version()`과 `render_legal_metadata_chunk_texts()`로 얻은 text
    목록 길이가 `chunk_part_count`와 같고, 해당 part text와
    `compute_content_hash(text)`가 실제 `text`·`content_hash`와 정확히 같다.
  - `legal_citation_url(law_type=..., source_sequence=...,
    effective_from=...)`가 metadata의 `source_url`과 정확히 같아야 한다.
  - `compute_document_id()` 결과가 `doc_id`와 같고,
    `compute_chunk_id_from_document_id(doc_id, heading_path, chunk_part,
    chunking_version)` 결과가 `chunk_id`와 같아야 한다. 임의 URL, ordinal, document/chunk
    ID는 N7에서 terminal `NO_EVIDENCE`/`fail`, N8에서 adapter 오류·전체 미병합이다.
- 새 N7에는 chunk ID 공식을 복제하지 않는다. `rag_design.chunking`에 정확히
  `compute_chunk_id_from_document_id(document_id, heading_path, part,
  config_version)`를 최소 추출하고, 기존 공개 `compute_chunk_id(Document, ...)`가 이
  helper에 delegate하게 해 API와 결과를 보존한다. N7 strict validator는 새 helper를
  직접 import한다. `vector_store.py`의 기존 private ingest inline 공식 통합은 이번
  N7/N8 범위 밖이며 변경 파일에 추가하지 않는다.
- N7이 참조한 current law chunks를 revision key
  `(law_type, source_id, source_sequence)`로 묶는다. 같은 revision의 모든 chunk는
  top-level `schema_version`, `doc_id`, `source_type`, `heading_path`,
  `citation_locator`가 같고, metadata는 `chunk_part`만 제외한 전체 mapping이 정확히
  같아야 한다. part별로 달라도 되는 값은 top-level `chunk_id`, `text`, `ordinal`,
  `content_hash`와 metadata의 `chunk_part`뿐이며 각각 위 deterministic 검증을 통과해야
  한다. parent invariant 불일치나 같은 `chunk_part`를 서로 다른 evidence payload가
  가리키는 경우는 `CONFLICT`/`fail`이다. 서로 다른 canonical part는 허용하며 retrieval
  subset일 수 있으므로 part 번호의 연속성이나 전체 part 수만큼의 수집을 요구하지 않는다.
  `RetrievedChunk.rank`, `score`, `score_type`, `retriever_version` telemetry는 revision
  parent 비교에서 제외하고 query/source/index provenance는 앞선 공통 검증으로 별도
  확인한다.
- 날짜·interval 검증으로 current 후보를 정한 뒤, 같은 revision에 둘 이상이 있으면
  parent/part coherence를 개별 canonical payload·ID 실패 매핑보다 먼저 판정한다. 따라서
  같은 revision의 parent field가 다르거나 같은 part가 상이한 payload를 가리키면 N7은
  `CONFLICT`; coherent group 안의 단독 arbitrary URL/ID/ordinal/text 위반은 terminal
  `NO_EVIDENCE`다. 이 precedence로 same-sequence/different-doc·metadata 사례가 단순
  fabricated hit로 낮아지지 않게 한다.
- claim별 `legal_metadata`는 선언한 모든 expected pair가 위 검증을 통과한 현재
  canonical law evidence로 덮일 때만 supported다. clean provenance, 유효한 subsidy
  support, metadata-only aspect, retry count 0인 상태에서 일부 expected pair가 전혀
  resolve되지 않은 경우에만 `insufficient_law`가 가능하다. retry가 1이거나 invalid·
  unexpected evidence가 있으면 `fail`이다.
- policy 판정은 기존 `EvidenceState`, `AbstentionDecision`, `decide_abstention()`을
  재사용한다. 법령 capability는 `supported_legal_evidence_aspects()`로만 계산한다.
- policy 결과는 `SAFETY | CONFLICT | STALE -> fail`, terminal provenance 오류도
  `NO_EVIDENCE -> fail`, 문서 missing이 있는
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
- safety fail은 유효한 retry count를 보존해 출력한 뒤 query/claim/chunk 입력을 읽지
  않는다. 따라서 unsafe 요청은 누락된 query/plan보다 먼저 `SAFETY -> fail`한다.
- `route_evidence_gate()`는 `insufficient_document -> document_verification`,
  `insufficient_law -> targeted_law_search`, `pass -> eligibility_verdict`,
  `fail -> terminal`을 정확히 반환하며 verdict 누락·미지원 값은 입력 오류로 거부한다.
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
- `supported_aspects`는 전체 claim의 요구 목록을 그대로 복사하지 않고 claim별 실제
  검증 결과로 계산한다. 예를 들어 선언한 law pair의 canonical metadata는 모두 있지만
  `required_aspects == ["legal_metadata", "legal_article_body"]`이면 decision의
  `missing_aspects`는 `legal_article_body`만 포함한다. 이 경우에도 article capability가
  없으므로 N8 없이 `fail`한다. 최종 `missing_aspects`는 claim별 missing의 결정론적
  합집합이며 한 claim의 law evidence로 다른 claim의 부족분을 가리지 않는다.
- 법령 metadata 보충 후 `pass`하려면 N5/N6의 실제 matching subsidy evidence가
  substantive claim을 이미 `supported`하고, 남은 명시적 요구가
  `legal_metadata`뿐이며 선언한 모든 `required_law_sources`가 충족되어야 한다. law
  chunk만으로 substantive claim을 통과시키지 않는다.

### N8 표적 법령 검색

- N8은 N7의 정상 `insufficient_law` 출력에서만 진입한다. 다음을 search 전에 직접
  검증한다: 명시적 `safety_blocked is False`,
  `evidence_gate_verdict == "insufficient_law"`, 실제 int
  `law_retry_count == 1`, 실제 `AbstentionDecision`의 `abstain is True`와 reason
  `NO_EVIDENCE`, 정확한 빈 `list`인 `missing_document_claim_ids == []`.
- N8은 `missing_law_claim_ids`를 신뢰하지 않는다. 값은 비어 있지 않은 ordered unique
  `list[str]`이며 각 ID도 공백이 아니어야 한다. N7과 공유하는 단일 provenance/coverage
  resolver로 현재 state의 clean evidence, substantive subsidy support와 expected law
  pair coverage를 다시 계산한다. 그 결과의 ordered missing target IDs와 입력 목록이
  정확히 같아야 한다. malformed/unknown/duplicate/non-law/stale target, capability·
  subsidy 전제 불일치, 누락·추가·순서가 다른 target은 명시적 입력 오류다.
- 각 target은 `law_check_required=True`, `required_aspects`가 정확히
  `["legal_metadata"]`, nonempty ordered-unique `required_law_sources`를 가져야 한다.
  이미 모든 expected pair가 충족된 target은 stale target이며 검색하지 않는다.
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
- 질의 text는 optional `ClaimDraft.search_query`의 trim 결과를 우선한다. 없거나 비어
  있으면 대상 claim이 이미 참조한 subsidy evidence IDs 중 공통 입력 계약을 통과하고
  `chunk.metadata["source_id"] == policy_id`이고 canonical parent `doc_id`를 가진
  `chunk.text`만 state 입력 순서대로 결합한다. 같은 policy의 미참조 chunk는 사용하지
  않는다. 검증된 claim-bound text도
  없으면 검색어나 법령명을 추정하지 않고 해당 claim을 미충족으로 남겨 N7이
  fail-closed 한다. 비문자열 `search_query`는 명시적 입력 오류다.
- 검색은 target claim 순서와 그 claim의 `required_law_sources` 순서를 보존해, 아직
  충족되지 않은 pair마다 별도 호출한다. filter identity는 producer 선언에서만 오며
  top hit에서 역으로 고르지 않는다. 호출은 정확히 다음 의미를 갖는다.

  ```python
  source = SourceType(route_indexes(QueryScope.LAW)[0])
  search(
      source,
      query,
      query_id=query_id,
      search_filter=VectorSearchFilter(
          as_of=as_of_date,
          metadata_equals={
              "law_type": required_source["law_type"],
              "source_id": required_source["source_id"],
          },
      ),
  )
  ```

- 각 검색 결과는 claim에 넣기 전에 다음을 모두 검증한다.
  - `RetrievedChunk.query_id == state.query_id`
  - chunk `source_type is SourceType.LAW`이고 `index_name == "law"`
  - metadata `(law_type, source_id)`가 현재 검색한 expected pair와 정확히 같음
  - `supported_legal_evidence_aspects()`가 canonical metadata-only chunk로 인정
  - N7과 동일한 strict date/hash/document-ID 계약을 통과하고 `as_of`가 chunk의
    `[effective_from, effective_to)` 안에 있음
  - N7과 같은 official `source_url`, part range/count, `ordinal`, rendered text/hash와
    deterministic document/chunk ID 계약을 통과함
- 잘못된 결과나 search callable 예외는 빈 결과로 바꾸지 않고 명시적 오류로 전파한다.
  wrong subtype/source ID를 포함한 identity mismatch도 오류다. 정상적인 빈 pair 검색이
  하나라도 나오면 그 N8 실행에서 앞서 수집한 결과까지 claim/law chunks에 merge하지
  않고 즉시 N7로 돌아간다. N7은 count가 이미 1이므로 계속 missing인 pair를 보고
  `fail`한다.
- N8은 기존 `law_chunks`와 이번 실행의 검증된 신규 결과를 합친 뒤, 어떤 dedupe나 claim
  evidence ID 추가보다 먼저 동일 `chunk_id` payload를 비교한다. evidence-relevant
  payload는 `RetrievedChunk.chunk` 전체, `query_id`, `chunk.source_type`, `index_name`이다.
  이 값들이 모두 같을 때만 `rank`, `score`, `score_type`, `retriever_version` 같은
  retrieval telemetry 차이를 무시하고 first-seen 항목으로 dedupe한다. 하나라도 다르면
  같은 ID가 다른 근거를 가리키는 adapter/provenance 오류로 전체 N8 update를 merge하지
  않는다.
- 같은 pre-dedupe 후보 집합에서 `(law_type, source_id)`별 current
  `source_sequence` 집합도 먼저 계산한다. 둘 이상이면 dedupe로 숨기지 않고 conflict로
  처리하며 전체 N8 update를 merge하지 않는다. 이미 state에 들어온 conflict는 N7이
  `CONFLICT`/`fail`, N8이 새 결과에서 발견한 conflict는 명시적 conflict 오류로
  전파한다.
- 같은 pre-dedupe 후보를 revision key로도 묶어 N7과 동일한 top-level·metadata parent
  invariant와 part uniqueness를 확인한다. 같은 revision의 서로 다른 canonical part는
  허용하지만 parent invariant가 다르거나 같은 part가 서로 다른 payload를 가리키면
  adapter/conflict 오류로 전체 update를 merge하지 않는다. evidence-relevant payload가
  완전히 같고 retrieval telemetry만 다른 동일 `chunk_id` 반복 hit는 기존 계약대로 한
  payload로 보고 first-seen dedupe한다. subset retrieval에는 contiguous/full-part 조건을
  추가하지 않는다.
- 검증된 검색 결과 ID만 대상 claim의 기존 `evidence_chunk_ids` 뒤에 합치고
  `chunk_id` 기준 최초 순서를 보존해 dedupe한다. N8은 `status`를 supported로 확정하지
  않으며 기존 substantive `status`도 임의 변경하지 않는다.
- 반환 `law_chunks`는 기존 전체 목록 뒤에 신규 결과를 붙이고 `chunk_id` 기준 최초
  항목을 보존해 dedupe한다. `claim_plan`은 정상 빈 결과와 성공 결과 모두
  `copy.deepcopy()`한 뒤 반환·수정해 nested `evidence_chunk_ids`, `reasons`,
  `required_law_sources` 목록·dict가 입력 claim과 alias되지 않게 한다. law chunk는 기존
  read-only value 계약을 유지해 list만 새로 만들고 전체 deep copy하지 않는다. 다른
  state 필드는 partial merge로 보존한다.

### State 확장

기존 `TypedDict(total=False)`에 필요한 producer/output 필드만 추가한다.

- `ClaimType = Literal["eligibility", "amount", "duplicate"]`
- total `LawSourceRef`: `law_type: Literal["law", "admrul", "ordin"]`,
  `source_id: str`
- `EvidenceGateVerdict = Literal["pass", "insufficient_document",
  "insufficient_law", "fail"]`
- N7/N8 evidence binding의 `ClaimDraft.policy_id`는 안정 원천 ID인 chunk metadata의
  `source_id`; 부모 subsidy 문서의 버전 포함 `Chunk.doc_id`는 이 원천 ID와
  canonical하게 일치하는지 별도 검증
- `ClaimDraft`: `claim_type: ClaimType`, `search_query: str`,
  `required_aspects: list[str]`, `required_law_sources: list[LawSourceRef]`
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
| E9의 `doc_check_required=False`가 검증 bypass처럼 보인다. | N5 supported 상태·이유·실제 query/policy-matched 및 canonical parent subsidy evidence가 모두 있어야 인정한다. |
| 법령 검색 대상 identity가 claim에 없다. | N5/N6가 official metadata 기반 ordered `required_law_sources`를 선언하고 N8은 exact pair filter만 사용한다. top hit 역선택은 금지한다. |
| fabricated ID와 valid ID가 함께 있으면 valid 하나로 통과할 수 있다. | 모든 declared evidence ID를 union에서 exactly-once resolve하고 unknown/dual/cross-query 등 provenance 오류는 retry 없이 fail한다. |
| 같은 법령 identity의 여러 현행 sequence가 동시에 들어올 수 있다. | `(law_type, source_id)`별 current `source_sequence`를 dedupe 전에 계산해 둘 이상이면 임의 선택 없이 conflict/fail하고 N8은 전체 미병합한다. |
| 같은 `chunk_id`의 서로 다른 payload가 first-seen dedupe에 숨을 수 있다. | chunk 전체·query/source/index가 같은 duplicate만 허용하고 다른 payload는 dedupe 전 provenance 오류로 전체 미병합한다. |
| 같은 revision의 chunk parts가 서로 다른 parent metadata를 가리킬 수 있다. | revision key별 top-level parent fields와 `chunk_part` 외 metadata를 exact 비교하고, invariant mismatch·같은 part의 상이한 payload는 N7 conflict/N8 전체 미병합으로 처리한다. canonical subset parts는 허용한다. |
| parent `Document`가 없는 N7에서 chunk ID 공식을 새로 복제할 위험이 있다. | N7은 `compute_chunk_id_from_document_id()`를 사용하고 기존 `compute_chunk_id()`가 그 helper에 delegate한다. `vector_store.py`의 기존 private ingest 공식 통합은 범위 밖이다. |
| N8의 shallow claim copy가 nested producer state를 alias할 수 있다. | 빈 결과와 성공 결과 모두 `copy.deepcopy(claim_plan)`을 반환하고 immutable/read-only law values는 deep copy하지 않는다. |
| N8에 검색 가능한 claim 문장이 없다. | optional `search_query`를 추가하고 검증된 matching subsidy text만 deterministic fallback으로 허용한다. |
| 그림의 법률 분류와 저장소 `law_type`이 다르다. | 저장소의 `law`, `admrul`, `ordin`과 전 subtype 공통 `source_sequence`만 사용한다. |
| law 검색 결과가 substantive 법률 주장도 지지하는 것으로 오해될 수 있다. | subsidy evidence가 이미 substantive claim을 지지하고 남은 aspect가 `legal_metadata`일 때만 보충 가능하다. article/interpretation은 즉시 fail한다. |
| 기존 date filter는 문자열 앞 10자만 사용할 수 있다. | LAW는 `is_canonical_date()`와 기존 hash/document/canonical helpers로 먼저 strict 검증한 뒤 `[from,to)` filter를 적용한다. subsidy의 `None` 경계 계약은 그대로 둔다. |
| 실제 retriever 전달 경로가 없다. | factory 없이 keyword-only search callable을 받고 향후 `functools.partial`로 바인딩한다. |
| 이전 실행의 verdict/missing 값이 state에 남을 수 있다. | N7의 모든 정상 반환에서 relevant output key 전체를 명시한다. |

## 변경 파일과 심볼

| 파일 | 변경 |
| --- | --- |
| `rag_design/chunking.py` | `compute_chunk_id_from_document_id()` 최소 추출, 기존 `compute_chunk_id(Document, ...)` delegate로 API·결과 보존 |
| `src/rag_chatbot/graph/state.py` | `ClaimType`, total `LawSourceRef`, `EvidenceGateVerdict`, `ClaimDraft`, `GraphState` optional 필드 추가 |
| `src/rag_chatbot/graph/nodes/evidence_gate.py` | 공용 provenance/coverage resolver, `evaluate_evidence(state: GraphState) -> dict`, exact verdict router 구현 |
| `src/rag_chatbot/graph/nodes/targeted_law_search.py` | `LawSearch`, `search_targeted_laws(state, *, search)` 구현 |
| `src/rag_chatbot/graph/nodes/__init__.py` | 두 실제 노드 함수와 N7 verdict router re-export |
| `tests/test_evidence_gate_and_targeted_law_search.py` | fake searcher 기반 단일 `unittest` 파일 추가 |

구현 변경 파일은 위 6개로 고정한다. `rag_design`에서는 `chunking.py`의 공유 helper 추출
외에는 건드리지 않고 requirements, graph builder와 N5/N6/N9 파일은 변경하지 않는다.

## 구현 단계

1. `rag_design.chunking`에서 doc ID 기반 chunk ID helper를 추출하고 기존 API의 결과
   동등성을 고정한다.
2. `state.py`에 claim/source identity type, verdict와 optional producer/output 필드를
   추가한다.
3. N7 입력 검증에서 query ID, exact claim type, ordered unique claims/evidence/source
   pairs, safety bool, counters, canonical `as_of`를 확인한다.
4. N7/N8이 함께 쓰는 단일 resolver에서 declared evidence를 exactly-once resolve하고
   query/pool/source/policy/parent-doc/law identity provenance를 검증한다.
5. LAW는 기존 schema/hash/document/canonical/date/citation helpers와 공유 chunk ID
   helper를 조합해 per-hit deterministic 계약 및 revision/part coherence를 검증하고,
   subsidy는 기존 `MetadataFilter`/`chunk_matches_filter()`의 optional date 경계를 유지한다.
6. claim별 all-of law source coverage와 실제 supported aspects를 계산해 provenance
   terminal fail, 4-way verdict, retry 상한과 stale 방지 전체 output을 반환하고 exact
   verdict router를 제공한다.
7. N8에서 N7 provenance output과 exact target 재계산을 확인한 뒤 missing pair별 exact
   metadata filter로 direct callable을 호출하고, 결과 identity/contract 검증,
   same-ID payload·revision coherence·복수 current sequence pre-dedupe 검증 후 deep-copied
   claim별 evidence·전체 law chunk dedupe를 구현한다.
8. 실제 노드 함수와 verdict router를 `nodes/__init__.py`에서 re-export한다.
9. 단일 테스트 파일로 연결부·provenance 실패 경계·입력 불변성과 metadata-only 제한을
   검증한다.

## 테스트 매트릭스

| ID | 시나리오 | 핵심 확인 |
| --- | --- | --- |
| T0 | safe 입력의 `claim_plan` 누락·비-list·빈 목록, `claim_type` 누락·3값 외 값 | vacuous `pass` 없이 명시적 입력 오류, `eligibility`, `amount`, `duplicate`만 허용 |
| T1 | E9 직접 claim의 유효한 N5 subsidy evidence | 날짜 경계가 없는 기존 subsidy fixture도 unbounded로 `pass`, ordered plan과 state 불변 |
| T2 | E9 status/reasons/evidence 중 하나 부족 | 최초 `insufficient_document`, count 소진 후 `fail` |
| T3 | 유효 subsidy + nonempty exact `required_law_sources`, law metadata 최초 부족 | clean provenance에서만 `insufficient_law`, count 1, 정확한 대상 IDs |
| T4 | exact `(law_type, source_id)` canonical evidence 또는 N8 exact-pair 보충 | all-of pair가 모두 있을 때만 N7 `pass`; law-only substantive pass 불가 |
| T5 | N8 pair 검색의 정상 빈 결과 | 해당 실행의 수집 결과 전체 미병합, 다음 N7에서 retry 소진 `fail`, 추가 루프 없음 |
| T6 | safety 누락·비-bool·True, unsafe 요청의 query/plan 누락 | 유효 count를 보존하고 입력 parsing보다 먼저 SAFETY `fail` |
| T6-A | N7 verdict route와 누락·미지원 verdict | 4개 verdict를 exact node label로 매핑하고 그 외 값은 입력 오류 |
| T7 | conflict status 또는 한 law identity의 복수 current `source_sequence` | 임의 버전 선택 없이 즉시 CONFLICT `fail` |
| T8 | `as_of`와 source별 interval 경계 | subsidy `None` dates는 unbounded, 값이 있으면 `[from,to)`; LAW는 strict canonical `[from,to)` |
| T9 | counter 누락 및 잘못된 값 | 누락=0; bool·음수·비-int·1 초과는 명시적 오류 |
| T10 | duplicate claim/evidence ID와 duplicate/invalid `LawSourceRef` pair | 입력 오류, leading-zero source ID 보존, 입력 plan 불변 |
| T10-A | searchable claim의 source 목록 누락·빈 값, law-disabled claim의 nonempty 목록 | 전자는 identity 추정 없이 terminal fail/N8 미호출, 후자는 모순 입력 오류 |
| T11 | valid ID와 fabricated ID 혼합, unknown/dual/cross-query/wrong-pool, metadata source_id와 다른 policy_id, doc_id를 policy_id로 쓴 evidence, source/version과 불일치하는 parent doc_id | exact source_id policy match와 canonical parent doc_id 외에는 통과 금지; N7 terminal `fail`, missing 목록 empty, N8 미호출 |
| T12 | `required_aspects` 누락/빈값/미지원/flag 모순과 metadata+article 혼합 | article/interpretation은 N8 미호출; valid metadata가 있으면 `missing_aspects`에는 article만 남음 |
| T13 | `search_query`와 fallback | explicit query 우선, 검증된 matching subsidy text만 입력순 결합, 매핑 없으면 검색 안 함 |
| T14 | safe 입력의 query ID 누락·비문자열·공백 | evidence 검색/판정 전에 명시적 오류 |
| T15 | fake search 실제 호출 계약 | missing pair마다 LAW source, 동일 query ID, canonical as-of와 exact `law_type/source_id` filter |
| T15-A | N8 entry provenance 묶음 검증 | safety/verdict/count/NO_EVIDENCE/missing-docs, canonical as-of와 재계산 target exact equality가 틀리면 search 전 오류 |
| T16 | wrong subtype/unrelated source, non-ASCII·nondecimal source ID/sequence와 result mismatch | malformed/unexpected pair는 N7 terminal fail; N8 wrong pair 결과는 명시적 오류·미병합 |
| T17 | 기존+신규 same-ID law chunks와 same-part payload | evidence-relevant payload가 동일한 telemetry duplicate만 first-seen 허용; same-ID/different payload, 같은 revision·part의 상이한 payload와 복수 current sequence는 dedupe 전 거부·전체 미병합 |
| T18 | search 예외 | 그대로 전파하고 빈 결과로 은폐하지 않음 |
| T19 | input immutability, nested alias와 partial merge | 빈 검색·성공 검색 모두 반환 claim의 nested list/dict를 수정해도 입력 claim은 불변; law value는 그대로 재사용하고 `slots` 등 무관 state 유지, N7 output key 매번 초기화 |
| T20 | 여러 `required_law_sources`의 일부/전체 충족 | all-of라서 일부만 있으면 정확히 missing, unrelated top hit로 대체 금지, 전부 충족 시 pass |
| T21 | LAW date/hash/URL/deterministic identity/ordinal 변조 | date·interval, rendered text/hash, exact official citation URL, computed doc/chunk ID, part range/count와 `ordinal == chunk_part` 검증 |
| T22 | 같은 revision의 multi-part와 parent 불일치 | canonical한 서로 다른 subset parts는 pass; schema/doc/source/heading/locator, effective/date 등 `chunk_part` 외 metadata가 다르거나 같은 part가 상이한 payload면 N7 CONFLICT, N8 오류·전체 미병합 |
| T23 | chunk ID helper 호환성과 관련 회귀 | 같은 Document·heading·part·config에서 기존 `compute_chunk_id()`와 새 `compute_chunk_id_from_document_id()` 결과가 정확히 동일하고 기존 chunking/vector-store 관련 회귀가 유지됨 |

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
독립 QA에서 확인된 `.gitignore` 대상 `__pycache__` 7개 디렉터리는 계획·구현 변경물이
아니므로 삭제·stage·commit하지 않는다. 이후 검증은 위처럼
`PYTHONDONTWRITEBYTECODE=1`로 추가 bytecode 산출물을 만들지 않는다.

## 완료 기준

- N7이 E9 포함 모든 declared evidence ID를 state chunk union에서 exactly-once resolve하고
  query/pool/source/policy/parent-doc/expected-law-identity provenance를 검증한다.
- N7은 safe 입력에서 누락·비-list·빈 `claim_plan`을 거부해 빈 plan을 `pass`하지 않는다.
- N7은 필수 `claim_type` 3값과 ordered-unique all-of `required_law_sources`를 검증하고,
  fabricated+valid 혼합이나 unexpected law pair를 retry 가능한 missing으로 낮추지 않는다.
- N7이 4개 verdict, 종류별 retry 1회와 전체 relevant output key를 결정론적으로
  반환한다.
- N7 verdict router가 4개 verdict를 exact node label로 매핑하고 누락·미지원 값을
  거부한다.
- safety, conflict, canonical as-of와 source별 저장소 날짜 계약을 기존 filter helper로
  fail-closed 검증한다.
- LAW는 canonical date/effective interval, content hash, computed document ID와 canonical
  metadata-only projection, exact official citation URL, part/ordinal과 deterministic chunk
  ID를 기존·공유 helpers로 검증한다. 같은 identity의 복수 current sequence와 같은
  revision의 parent/part ambiguity는 conflict 처리하고 canonical subset parts는 허용한다.
  subsidy `None` dates는 기존 unbounded 계약을 유지한다.
- 새 N7은 chunk ID 공식을 복제하지 않고 `compute_chunk_id_from_document_id()`를 직접
  사용하며, 기존 `compute_chunk_id(Document, ...)`는 그 helper에 delegate해 API·출력을
  보존한다. `vector_store.py`의 기존 private ingest inline 공식 통합은 범위 밖이다.
- N8이 proven `insufficient_law` state의 missing pair만 exact identity filter로 검색하고
  검증된 결과만 순서 보존·dedupe 병합한다. top hit identity 역선택은 없다.
- N8은 combined 기존·신규 law candidates의 same-ID evidence payload와 identity별 current
  sequence, revision parent/part coherence를 dedupe 전에 검증하며, 불일치·conflict에서
  전체 update를 merge하지 않는다.
- N8은 verdict/count/decision/missing-docs, target 재계산과 as-of를 독립 검증해
  malformed/unknown/duplicate/non-law/stale/추가·누락 target을 검색하지 않는다.
- metadata-only 법령을 substantive 근거로 승격하지 않고 article/interpretation은
  검색 없이 fail한다. 실제로 지원된 claim별 aspect를 반영해 이미 충족된
  `legal_metadata`를 `missing_aspects`에 다시 넣지 않는다.
- 잘못된 입력과 검색 오류를 숨기지 않으며 입력 state와 무관 필드를 수정하지 않는다.
  N8의 빈 결과·성공 결과 claim plan은 nested alias가 없는 deep copy이고 law chunk value는
  불필요하게 복제하지 않는다.
- 신규 테스트와 관련 회귀가 통과하고 전체 suite의 기존 환경 실패를 구분해 기록한다.
- 새 의존성, factory, graph builder, LLM 질의 생성, N5/N6/N9 구현을 추가하지 않는다.
- N5/N6가 official structured metadata에서 `required_aspects`, nonempty
  `required_law_sources`와 유효한 subsidy evidence를 생산하지 않은 searchable law
  claim은 fail-closed된다. N8은 누락 identity를 추정하지 않는다. `search_query` 누락은
  검증된 policy-matched 및 canonical parent subsidy text가 있을 때만 예외이며, 그것도
  없으면 fail-closed다.
- graph ingress/선행 producer가 실제 bool `safety_blocked`와 canonical `as_of`를 반드시
  공급한다. unsafe default, `date.today()` 추정, 누락 시 subsidy-only claim을 우회하는
  fallback은 없으며 graph builder가 범위 밖인 이번 변경의 명시적 integration
  limitation이다.
- `git diff --check`가 통과하고 변경 파일이 계획된 범위에만 있다.

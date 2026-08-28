# 1차 RAG 설계 구현 명세

이 구현은 공통 계약과 검증 가능한 수집 결과 인수 정책, 임베딩 provider 인터페이스와 영속 Vector DB 동기화·검색까지 제공한다. 공개 API 수집기, LangChain/LLM 답변 생성, end-to-end 답변 파이프라인, Streamlit/UI와 운영 배포는 포함하지 않는다. 이 코드와 테스트·smoke 통과만으로 프로젝트 Gate 통과를 주장하지 않는다.

> [!IMPORTANT]
> 팀 회의에서 데이터 규모가 과도하다는 이유로 법령·행정규칙·자치법규의
> 본문과 조문을 수집·저장·임베딩·색인 대상에서 제외하기로 결정했다.
> 법령 문서에 본문이나 조·항·호·목 locator가 없는 것은 의도된
> `metadata_only` 계약이며 결함·파싱 실패·인수 또는 병합 차단 사유가 아니다.
> 목록 metadata로 확인할 수 없는 조문·법률 해석 질문은 보류하고 공식
> 상세 페이지에서 원문 확인을 안내한다.

## 구현 경계

| 영역 | 구현 | 파일 |
| --- | --- | --- |
| 공통 계약 | `Document`, `Chunk`, `RetrievedChunk`, `EvidenceCheckResult`, `Citation`, `AnswerResult` 및 JSON 직렬화 | `rag_design/contracts.py` |
| 인수 검증 | manifest·Document Card·필수 메타데이터·중복·hash·권리·민감정보·URL 검사 | `rag_design/validation.py` |
| 청킹 | 보조금 서비스 섹션, 법령 목록의 `기본정보` 단일 섹션 및 긴 구조 단위 fallback | `rag_design/chunking.py` |
| 인덱스 정책 | `subsidy`·`law` 논리 분리, 지역·시행일 필터, 교차 인덱스 점수 병합 제한 | `rag_design/index_policy.py` |
| 인용 | 공식 도메인 제한, 인증 파라미터 제거, 법령 유형별 직접 공개 상세 URL 생성 | `rag_design/citation.py`, `rag_design/url_safety.py` |
| 보류 | 근거 없음·핵심 조건 미충족·출처 충돌·최신성 불명확·안전 사유 판정 | `rag_design/policy.py` |
| 평가 | Recall@k, MRR@k, citation precision·coverage, 보류 precision·recall, 지연·오류율 | `rag_design/evaluation.py` |
| 임베딩·Vector DB | 교체 가능한 embedding provider, 논리 인덱스별 영속 동기화·필터·검색 | `rag_design/embeddings.py`, `rag_design/vector_store.py`, `rag_design/vector_cli.py` |

## 참조 구현 기본 정책

아래 법령 metadata 필드·ID·날짜·직접 URL과 `legal-metadata-v1`은 PR #8이
따라야 할 동결 통합 계약이다. 모델·embedding provider·검색 `top-k`·생성 문구·
UI와 운영 저장소는 이 계약을 소비하는 별도 runtime 선택이다.

- 계약 버전은 `1.0`이며 다른 버전은 명시적으로 거부한다. 법령 metadata 계약은 `legal-metadata-v1`이다. 공유·배포된 법령 인덱스가 없는 동결 전 계약 정정이므로 schema와 `structure-v2`, `chroma-vector-store-v3` 버전은 유지한다. 다만 law collection fingerprint가 달라지므로 기존 조문 기반 law registry·chunk·Vector DB는 전량 재생성해야 하며 subsidy 인덱스는 호환된다.
- JSON boolean 필드는 문자열 대체값을 허용하지 않으며, manifest `collected_at`은 timezone을 포함한 ISO 8601 datetime이어야 한다. 출처별 핵심 metadata는 정의된 문자열·목록·날짜 타입을 요구한다. 단, 지역을 확정하지 못한 보조금의 `region_names`는 빈 목록이어야 한다.
- `doc_id`는 출처·원천 ID·버전에서, `chunk_id`는 부모·구조 위치·청킹 설정에서 재현 가능해야 한다. 법령 ID는 `law:<law_type>:<source_id>:<source_sequence>:<effective_from>` 형식이다. `source_id`는 안정적인 entity ID이고 `source_sequence`는 공식 원천의 개정·버전 일련번호이므로 서로 대신 사용하지 않는다. chunk ID는 SHA-256으로 생성하며 프로세스마다 달라지는 Python `hash()`를 사용하지 않는다. 외부 chunk 묶음은 부모 문서에서 같은 설정으로 재생성한 결과와 대조한다.
- `content_hash`는 줄바꿈을 LF, Unicode를 NFC로 정규화한 UTF-8 `content`의 SHA-256과 일치해야 한다. 법령 `content`는 목록 metadata 요약이며 조문 본문이 아니다. 법령 원천 identity는 `source_type=law` 문맥의 `(law_type, source_id)`다. 같은 identity의 동일 content는 같은 원천 중복으로 거부하지만, 같은 숫자 `source_id`라도 subtype이 다르면 다른 원천이므로 subtype 간 동일 content는 `duplicate_content_candidate` warning으로 기록하고 인수한다.
- 수집 인수에는 출처별 manifest와 Document Card가 모두 필요하다. 법령 인계에서는 양쪽에 `content_level=metadata_only`가 있어야 하고 보조금 인계에는 이 필드를 요구하지 않는다. 권리·민감정보 검토가 끝난 공개 문서만 승인 대상으로 삼는다.
- `HandoffReport.issues`는 인수를 거부하는 blocking 오류이고 `HandoffReport.warnings`는 검토가 필요하지만 인수를 막지 않는 non-blocking 관측값이다. warning만 있는 문서는 `accepted_document_ids`에 포함하고 `require_accepted()`도 성공한다.
- 보조금은 지원 대상·지원 내용 섹션과 기관·지역 범위·정규 지역명·서비스 분류를 필수로 보존한다. `region_scope`는 `national | regional | unknown` 중 하나이고 각각 `region_names == ["전국"]`, 비어 있지 않은 정규 계층명 목록, `region_names == []`와 결합된다. `전국`은 다른 이름과 혼용하지 않는다. 원천에 신청 방법이 없으면 `missing_recommended_subsidy_section` warning으로 기록하고 문서는 인수한다.
- 법령·행정규칙·자치법규는 `content_level=metadata_only`, `law_type=law|admrul|ordin`, `law_name`, 숫자 문자열 `source_sequence`, `organization`, `document_kind`, `YYYY-MM-DD` 날짜 `issued_date`·`effective_date`, `revision_type`을 필수로 보존한다. `law_name`은 title과 정확히 같아야 한다. top-level `effective_from`은 필수이며 `effective_date`와 정확히 같아야 한다. 선택적인 `source_updated_at`과 `effective_to`도 값이 있으면 `YYYY-MM-DD`여야 하고, `effective_to`는 `effective_from`보다 뒤여야 한다. `source_id`는 안정적인 숫자 entity ID다.
- PR #8 하위 PR의 `src/rag_chatbot/collectors/law/filtered_to_document.py`는 법령·자치법규 `공포일자`와 행정규칙 `발령일자`를 `issued_date`로, 공통 `시행일자`를 `effective_date`와 `effective_from`으로 정규화한다. 원천에 없는 선택 날짜는 `null`로 두고 추정하지 않는다.

법령 `Document.content`는 `render_legal_metadata_summary(metadata)`가 다음
여섯 줄을 표시된 순서대로 LF로 연결한 결과와 정확히 같아야 한다.

```text
법령명: {law_name}
법령유형: {document_kind}
소관기관: {organization}
제개정구분: {revision_type}
공포·발령일: {issued_date}
시행일: {effective_date}
```

유일한 `기본정보/basic_info` 섹션도 이 content와 같아야 한다. 임의의
본문·조문 텍스트를 이 renderer 형식이나 `basic_info`로 감싸는 입력은 계약
위반으로 거부한다.

- 지역 필터는 `region_names`의 exact intersection을 사용하고 `전국` 문서는 모든 지역 필터에 일치한다. `unknown` 문서는 지역 필터에서 fail closed지만 지역 필터가 없는 검색에는 포함될 수 있다. 지역 코드는 원천별 선택적 보조 metadata로 보존할 수 있지만 공통 필터에는 사용하지 않는다.
- 수집기는 공식 시도명을 사용하고 시군구 이름을 `시도 시군구` 형식으로 완전 수식하며 상위 시도명을 목록에서 먼저 제공한다. 공통 validator는 공식 시도 prefix·NFC·공백·중복·상위 시도 순서를 검사하지만 시군구 전체 registry를 내장하지 않으므로 정규 이름 생성 책임은 수집기에 있다. 검색 호출자는 별칭을 정규 이름으로 변환해야 하며 `중구` 같은 모호한 단독 이름은 임의로 해석하지 않는다.
- 현행 시도 집합은 [행정안전부 2026-07-01 행정구역코드 변경](https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000052&nttId=127039)을 기준으로 `전남광주통합특별시`를 포함하고 폐지된 `광주광역시`·`전라남도`를 정규 이름으로 받지 않는다. 구 완전수식 시군구명은 공식 변경표로 하위 지역을 확정할 때만 새 이름으로 변환한다. 구 최상위명 단독을 새 특별시 전체로 확대 해석하지 못하면 `unknown`으로 둔다.
- 청킹은 구조 경계를 넘지 않는다. 보조금 chunk prefix에는 지역명을 포함한다. 구조 단위가 상한을 넘을 때만 `max_chars=800`, `overlap_chars=100` 기본값으로 내부 분할한다. 설정값은 chunk ID와 `chunking_version`에 포함하며 이후 Dev set 실험에서 한 조건씩 변경할 수 있다.
- 법령 문서는 `heading_path=["기본정보"]`, `section_type=basic_info`인 섹션을 정확히 하나만 가지며 섹션 content는 문서 content와 같아야 한다. 조문·본문 형태 섹션은 거부하고, 법령 chunk에는 위 필수 정규화 metadata를 모두 전파한다. 합의된 본문 미포함은 `parse_warnings` 대상이 아니다.
- 시행 유효 구간은 `[effective_from, effective_to)`로 해석한다. 시작일에는 유효하고 종료일에는 유효하지 않다.
- `subsidy`와 `law` 검색 점수는 직접 비교하지 않는다. 양쪽 결과는 `interleave` 또는 검증된 `reciprocal_rank` 방식만 허용한다.
- 공개 인용은 `data.go.kr`, `gov.kr`, `law.go.kr` 계열 HTTPS URL만 허용한다. `serviceKey`, `OC`, API key, token 등은 대소문자·URL 인코딩 변형까지 제거한다. 실행 환경에서 알고 있는 실제 secret 값은 `secret_values`로 전달해 URL 어느 위치에 있어도 인수를 거부한다.
- 법령 인용은 API 요청 URL이 아니라 유형별 공개 상세 URL을 만든다. `law`는 `https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=<source_sequence>&efYd=<effective_date: YYYYMMDD>`, `admrul`은 `https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=<source_sequence>`, `ordin`은 `https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=<source_sequence>`를 사용한다. 계약 날짜는 `YYYY-MM-DD`이며 `law` URL의 `efYd`만 검증된 시행일에서 하이픈을 제거한 `YYYYMMDD`다. `admrul`과 `ordin` 직접 URL에는 날짜 query가 없다. 이 인용은 목록 metadata와 공식 원문 확인 경로만 뒷받침하며 조문 인용이 아니다.
- 실행 가능한 근거 capability는 `legal_metadata`, `legal_article_body`, `legal_interpretation`으로 구분한다. `supported_legal_evidence_aspects(chunks)`는 검증된 metadata-only 법령 chunk에서 `legal_metadata`만 도출한다. `legal_article_body` 또는 `legal_interpretation`이 요구되면 `decide_abstention`은 `abstain=true`, `reason=NO_EVIDENCE`와 누락 aspect를 담은 결정만 반환한다. 호출자가 답변 생성을 막고, 후속 생성·UI 계층이 공식 상세 페이지 안내를 렌더링한다. `policy.py`와 PR #8 수집기는 사용자 문구나 UI를 제공하지 않는다.
- 보류 판단에 검색 점수 하나를 사용하지 않는다. 안전 차단, 근거·핵심 조건 부족, 미해결 충돌, 최신성 미확인 순으로 검사한다. 조문 내용, 법적 정의·자격·배제·금지와 법률 해석이 필요한 질문에 목록 metadata만 있으면 위 capability 경계에 따라 보류한다. 파이프라인 오류는 보류와 별도 집계한다.

## 데이터와 병합 경계

- 법령·행정규칙·자치법규의 본문·조문은 수집 산출물, fixture, 임베딩과 Vector DB에 포함하지 않는다.
- 전체 생성 JSONL, 원천 응답과 런타임 Vector DB는 Git에 추적하지 않는다. 저장소에는 공개 가능한 대표 샘플과 manifest·Document Card만 포함한다.
- Document Card에는 목록조회 전용 범위, 본문 제외 이유, 정제·청킹 방법과 지원하지 않는 질문을 명시한다.
- `OC`, API key 등 인증정보가 있는 URL은 저장하지 않고 유형별 공개 상세 URL로 재구성한다.
- 병합 순서는 PR #13 팀 리뷰·승인 → `main` 병합 → 사용자 반영 확인 → PR #8 `feat/6-law-collection` 동기화 → PR #8 대상 하위 PR 팀 리뷰·병합 → 갱신된 PR #8 전체 검증 → PR #8의 `main` 병합이다.

## 평가 분모

- 검색 정답 근거가 없는 질문은 검색 Recall@k·MRR@k 분모에서 제외하고, 평가된 질문 수를 함께 반환한다.
- citation precision은 주장별 citation 연결 쌍을 분모로 하고 해당 주장의 gold 근거와 일치할 때만 정답으로 센다. coverage는 인용이 필요한 주장 수를 분모로 사용한다.
- 보류 precision은 실제 보류 수, recall은 정답상 보류해야 하는 질문 수를 분모로 사용하며 파이프라인 오류는 제외한다.
- 모든 지표에서 분모가 0이면 점수를 `0.0`으로 반환해 근거 없는 만점을 만들지 않는다.
- 지연 시간 표본이 없으면 p50·p95·오류율은 `0.0`, 표본 수는 `0`이다. p95는 nearest-rank 방식이다.

## 검증

공개 가능한 보조금 서비스와 `law`, `admrul`, `ordin` 목록 metadata 대표 문서, 각 manifest·Document Card가 `tests/fixtures`에 있다. 법령 fixture에는 본문·조문과 실제 API 키·인증 URL을 포함하지 않는다.

```powershell
python -m unittest discover -s tests -v
```

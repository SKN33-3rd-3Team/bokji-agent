# 1차 RAG 설계 구현 명세

이 구현은 공통 계약과 검증 가능한 수집 결과 인수 정책, 임베딩 provider 인터페이스와 영속 Vector DB 동기화·검색까지 제공한다. 공개 API 수집기, LangChain/LLM 답변 생성, end-to-end 답변 파이프라인, Streamlit/UI와 운영 배포는 포함하지 않는다. 이 코드와 테스트·smoke 통과만으로 프로젝트 Gate 통과를 주장하지 않는다.

## 구현 경계

| 영역 | 구현 | 파일 |
| --- | --- | --- |
| 공통 계약 | `Document`, `Chunk`, `RetrievedChunk`, `EvidenceCheckResult`, `Citation`, `AnswerResult` 및 JSON 직렬화 | `rag_design/contracts.py` |
| 인수 검증 | manifest·Document Card·필수 메타데이터·중복·hash·권리·민감정보·URL 검사 | `rag_design/validation.py` |
| 청킹 | 보조금 서비스 섹션, 법령 조·항·호·목 경계 우선 및 긴 구조 단위 fallback | `rag_design/chunking.py` |
| 인덱스 정책 | `subsidy`·`law` 논리 분리, 지역·시행일 필터, 교차 인덱스 점수 병합 제한 | `rag_design/index_policy.py` |
| 인용 | 공식 도메인 제한, 인증 파라미터 제거, 법령 공개 열람 URL 생성 | `rag_design/citation.py`, `rag_design/url_safety.py` |
| 보류 | 근거 없음·핵심 조건 미충족·출처 충돌·최신성 불명확·안전 사유 판정 | `rag_design/policy.py` |
| 평가 | Recall@k, MRR@k, citation precision·coverage, 보류 precision·recall, 지연·오류율 | `rag_design/evaluation.py` |
| 임베딩·Vector DB | 교체 가능한 embedding provider, 논리 인덱스별 영속 동기화·필터·검색 | `rag_design/embeddings.py`, `rag_design/vector_store.py`, `rag_design/vector_cli.py` |

## 참조 구현 기본 정책

아래 값은 실행 가능한 참조 구현의 기본값이며, 팀 승인 전 프로젝트의 최종 설정으로 간주하지 않는다.

- 계약 버전은 `1.0`이며 다른 버전은 명시적으로 거부한다.
- JSON boolean 필드는 문자열 대체값을 허용하지 않으며, manifest `collected_at`은 timezone을 포함한 ISO 8601 datetime이어야 한다. 출처별 핵심 metadata는 정의된 문자열·목록·날짜 타입과 비어 있지 않은 값을 요구한다.
- `doc_id`는 출처·원문 ID·버전에서, `chunk_id`는 부모·구조 위치·청킹 설정에서 재현 가능해야 한다. chunk ID는 SHA-256으로 생성하며 프로세스마다 달라지는 Python `hash()`를 사용하지 않는다. 외부 chunk 묶음은 부모 문서에서 같은 설정으로 재생성한 결과와 대조한다.
- 본문 `content_hash`는 줄바꿈을 LF, Unicode를 NFC로 정규화한 UTF-8 본문의 SHA-256과 일치해야 한다. 같은 `source_id + source_updated_at + effective_from`, 같은 문서 ID, 같은 본문 hash와 같은 chunk ID는 중복으로 거부한다.
- 수집 인수에는 출처별 manifest와 Document Card가 모두 필요하다. 권리·민감정보 검토가 끝난 공개 문서만 승인 대상으로 삼는다.
- 보조금은 지원 대상·지원 내용·신청 방법 섹션과 기관·지역코드·서비스 분류를 보존한다. 법령은 법령명·일련번호·공포일·시행일·개정 상태와 조문 위치를 보존한다.
- 청킹은 구조 경계를 넘지 않는다. 구조 단위가 상한을 넘을 때만 `max_chars=800`, `overlap_chars=100` 기본값으로 내부 분할한다. 설정값은 chunk ID와 `chunking_version`에 포함하며 이후 Dev set 실험에서 한 조건씩 변경할 수 있다.
- 법령 경로는 `조→항→호→목` 계층을 따르되 `조→호`도 허용하고, `목`은 반드시 `호` 다음에 둔다. 마지막 경로 수준은 section type과 일치해야 하며 모든 locator와 본문이 원문에 같은 순서로 존재해야 한다.
- 시행 유효 구간은 `[effective_from, effective_to)`로 해석한다. 시작일에는 유효하고 종료일에는 유효하지 않다.
- `subsidy`와 `law` 검색 점수는 직접 비교하지 않는다. 양쪽 결과는 `interleave` 또는 검증된 `reciprocal_rank` 방식만 허용한다.
- 공개 인용은 `data.go.kr`, `gov.kr`, `law.go.kr` 계열 HTTPS URL만 허용한다. `serviceKey`, `OC`, API key, token 등은 대소문자·URL 인코딩 변형까지 제거한다. 실행 환경에서 알고 있는 실제 secret 값은 `secret_values`로 전달해 URL 어느 위치에 있어도 인수를 거부한다.
- 법령 인용은 API 요청 URL이 아니라 `https://www.law.go.kr/lsInfoP.do?lsiSeq=...&efYd=...` 형식의 공개 열람 URL을 만든다.
- 보류 판단에 검색 점수 하나를 사용하지 않는다. 안전 차단, 근거·핵심 조건 부족, 미해결 충돌, 최신성 미확인 순으로 검사한다. 파이프라인 오류는 보류와 별도 집계한다.

## 평가 분모

- 검색 정답 근거가 없는 질문은 검색 Recall@k·MRR@k 분모에서 제외하고, 평가된 질문 수를 함께 반환한다.
- citation precision은 주장별 citation 연결 쌍을 분모로 하고 해당 주장의 gold 근거와 일치할 때만 정답으로 센다. coverage는 인용이 필요한 주장 수를 분모로 사용한다.
- 보류 precision은 실제 보류 수, recall은 정답상 보류해야 하는 질문 수를 분모로 사용하며 파이프라인 오류는 제외한다.
- 모든 지표에서 분모가 0이면 점수를 `0.0`으로 반환해 근거 없는 만점을 만들지 않는다.
- 지연 시간 표본이 없으면 p50·p95·오류율은 `0.0`, 표본 수는 `0`이다. p95는 nearest-rank 방식이다.

## 검증

공개 가능한 보조금 서비스 1건과 법령 조문 1건, 각 manifest·Document Card가 `tests/fixtures`에 있다. 실제 API 키나 인증 URL은 포함하지 않는다.

```powershell
python -m unittest discover -s tests -v
```

# 보조금24 데이터 수집 (Gate 1)

`data.go.kr`의 보조금24 Open API에서 정책 데이터를 모아, RAG 설계팀과 합의한
`Document` 스키마(JSONL)로 변환한다.

## 실행 순서

```bash
pip install -r requirements-collection.txt
```

`.env`에 아래 값을 채운다 (`.env.example` 참고):

```
GOV24_SERVICE_KEY=발급받은_인증키
# 선택: 시군구 5자리 법정동코드까지 채우려면 CSV 경로를 지정한다.
# 없으면 시도 2자리 코드까지만 채워진다.
SIGUNGU_CODE_CSV=
```

### 한 번에 전부 실행 (권장)

저장소 루트에서 `src`를 import 경로에 넣고 패키지 진입점을 실행하면 목록조회
-> 상세조회 -> 지원조건조회 -> 병합 -> Document 변환을 순서대로 실행한다.

```bash
$env:PYTHONPATH="src"

# 전체 실행 (10,957건 전부, 시간이 오래 걸림)
python -m rag_chatbot.collectors.gov_24

# 테스트: 목록 앞 50건만으로 파이프라인 전체를 빠르게 확인
python -m rag_chatbot.collectors.gov_24 --limit 50
python -m rag_chatbot.collectors.gov_24 -n 50
```

`--limit`/`-n`을 주면 상세조회·지원조건조회뿐 아니라 병합·Document 변환까지
전부 목록 앞 N건 기준으로만 처리한다(상세조회는 N건만 받았는데 병합은
전체를 대상으로 하면, 나머지가 "조회 안 함"이 아니라 "원천 결측"으로 잘못
표시되기 때문에 병합 단계도 같은 N을 쓴다).

### 단계별로 하나씩 실행하고 싶을 때

저장소 루트에서, `src`를 `PYTHONPATH`에 넣고 실행한다 (Windows PowerShell 기준
`$env:PYTHONPATH="src"`, macOS/Linux/Git Bash 기준 `PYTHONPATH=src`):

```bash
# 1. 목록조회 (전체 정책 목록)
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.gov24 list

# 2. 상세조회 + 지원조건조회 (전체 페이지 순회)
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.gov24 detail
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.gov24 conditions

# 3. 세 결과를 서비스ID 기준으로 병합 (테스트 시 뒤에 건수를 붙이면 앞 N건만)
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.merge_gov24
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.merge_gov24 50

# 4. RAG 설계팀이 쓰는 Document 스키마로 변환
PYTHONPATH=src python -m rag_chatbot.collectors.gov_24.to_document
```

`detail`/`conditions` 전체 실행은 페이지 단위로 다시 받아 원본 파일을
갱신한다. 실패 ID 전용 명령은 `cond[서비스ID::EQ]` 단건 필터를 사용한다.

## 산출물

| 파일 | 내용 | 저장소 포함 여부 |
| --- | --- | --- |
| `data/raw/gov24_*.json` | API 원본 응답 | 아니오 (재생성 가능) |
| `data/raw/gov24_*_failed_ids.json` | 상세조회/지원조건조회 중 API 호출이 끝내 실패한 서비스ID 목록 | 아니오 (재생성 가능) |
| `data/raw/gov24_merged.json` | 서비스ID 기준 병합 | 아니오 (재생성 가능) |
| `data/processed/subsidy_documents.jsonl` | `Document` 스키마 전체(현재 10,968건) | 아니오 (재생성 가능) |
| `data/processed/subsidy_manifest.json` | 매니페스트 + Document Card | **예** |
| `data/processed/subsidy_parse_warnings.json` | 제외된 항목·형식 경고 전체 목록 | 아니오 (재생성 가능) |
| `data/samples/subsidy_documents_sample.jsonl` | 형식 확인용 샘플 5건 | **예** |

## 알아둘 것

- `serviceDetail`/`supportConditions` 전체 수집은 `page`/`perPage`로 순회한다.
  단건 재시도만 `cond[서비스ID::EQ]`를 사용한다. `servId`는 서버가 무시해
  첫 페이지가 반복되므로 사용하지 않는다.
- `Document.source_url`은 반드시 공식 도메인(`data.go.kr`/`gov.kr`/`law.go.kr`)
  이어야 해서, "온라인신청사이트URL"이 아니라 "상세조회URL"(gov.kr)을 쓴다.
  공개 URL은 공통 안전 정책으로 HTTPS canonical URL로 만들며 인증 쿼리와
  구성된 실제 secret이 외부 문서나 경고에 노출되지 않게 한다.
- `수정일시` 필드는 레코드마다 형식이 달라(`YYYY-MM-DD` 또는 14자리 숫자)
  `to_document.py`에서 ISO 8601로 정규화한다.
- `BASE_URL`, 파라미터 이름은 공공데이터 API의 흔한 패턴을 예시로 적어둔
  것이며, 실제 활용가이드 문서 기준으로 이미 검증했다(2026-08-26 기준 정상
  동작 확인).

### 재시도·재실행

- 요청 하나가 실패하면(`gov24.py`) 지수 백오프로 최대 `MAX_RETRIES`(기본
  3)번까지 같은 요청을 재시도한 뒤에도 실패해야 `data/raw/gov24_*_failed_ids.json`에
  최종 실패로 기록한다.
- `detail`/`conditions`를 다시 실행하면, `data/raw/gov24_service_*.json`에
  이미 저장된 성공 결과는 그대로 재사용하고 재호출하지 않는다. 직전 실행의
  실패 목록에 있던 서비스ID만 다시 시도한다. 서비스ID를 key로 병합하기 때문에
  몇 번을 재실행해도 같은 서비스ID가 중복 레코드로 쌓이지 않는다.

### 지역 정보

- 지역 범위는 구조화된 필드가 없어 `region_utils.py`가 `소관기관명`에서
  추출한다. 확인된 중앙기관은 `region_scope=national`, `region_names=["전국"]`,
  시도/시군구는 `regional`과 상위 지역을 포함한 계층명으로 기록한다.
  확정할 수 없는 기관은 전국으로 확대하지 않고 `unknown`, `[]`로 보존한다.
- 현행 시도 2자리 코드(`region_sido_code`, 강원 `51`, 전북 `52`,
  전남광주통합특별시 `12`)는 보조정보로 채우지만, 시군구
  5자리 코드(`region_sigungu_code`)는 정확성이 중요해 하드코딩하지 않는다.
  `SIGUNGU_CODE_CSV` 환경변수로 data.go.kr "전국 법정동코드 전체자료" CSV
  경로를 지정하면 채워지고, 지정하지 않으면 `None`으로 남는다.

### 결측 vs 조회 실패

- `serviceDetail`/`supportConditions` 호출이 끝내 실패한 서비스ID는
  `data/raw/gov24_*_failed_ids.json`에 저장되고, 해당 문서의
  `metadata.field_status`와 `parse_warnings`에 "조회 실패로 누락"이라고
  표시해 원천 결측(`missing_source`)과 구분한다.
- `metadata.field_status`는 문서별로 섹션 필드, 근거법령(법령/행정규칙/
  자치법규) 그룹, 지원조건(JA코드) 필드 각각의 상태를 `present` /
  `missing_source` / `fetch_failed`로 담는다. 근거법령은 세 필드 중 하나만
  있어도 `present`로 취급하고, 개별 필드 단위로는 결측 경고를 만들지 않는다.

### 중복

- 서로 다른 서비스ID의 본문이 완전히 겹치더라도 삭제·제외하지 않는다.
  `to_document.py`가 `content_hash`가 같은 다른 서비스ID를
  `metadata.duplicate_content_of_source_ids`에 표시만 하고 그대로 보존한다.

### 경고 로그

- 제외 사유(서비스ID 없음, 공식 도메인 아님, 본문 없음 등)와 형식 인식 실패는
  전부 `data/processed/subsidy_parse_warnings.json`에 저장된다(콘솔에는 요약만
  출력). `subsidy_manifest.json`의 `manifest.parse_error_count`는 이 경고
  전체 건수를 담는다.

### `Document` dataclass 변환

- 이 스크립트는 `rag_design.contracts.Document`를 직접 import하지 않고 같은
  모양의 dict를 만들기만 한다(브랜치 미병합 상태에서 의존성을 피하기 위함).
  `rag_design.validation.validate_collection_handoff`는 dict가 아니라
  `Document` 인스턴스를 요구하므로, 실제 Gate 1 인수 검증은 두 브랜치가
  합쳐진 뒤 `Document.from_dict(json.loads(line))`로 변환하는 단계를 거쳐야
  한다. 지금 만드는 JSONL은 그 변환·검증을 아직 통과시켜보지 않은 상태다.

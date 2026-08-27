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
```

저장소 루트에서, `src`를 `PYTHONPATH`에 넣고 실행한다 (Windows PowerShell 기준
`$env:PYTHONPATH="src"`, macOS/Linux/Git Bash 기준 `PYTHONPATH=src`):

```bash
# 1. 목록조회 (전체 정책 목록)
PYTHONPATH=src python -m rag_chatbot.collectors.gov24 list

# 2. 상세조회 + 지원조건조회 (서비스ID당 1건씩, 병렬 처리)
PYTHONPATH=src python -m rag_chatbot.collectors.gov24 detail
PYTHONPATH=src python -m rag_chatbot.collectors.gov24 conditions

# 3. 세 결과를 서비스ID 기준으로 병합
PYTHONPATH=src python -m rag_chatbot.collectors.merge_gov24

# 4. RAG 설계팀이 쓰는 Document 스키마로 변환
PYTHONPATH=src python -m rag_chatbot.collectors.to_document
```

## 산출물

| 파일 | 내용 | 저장소 포함 여부 |
| --- | --- | --- |
| `data/raw/gov24_*.json` | API 원본 응답 | 아니오 (재생성 가능) |
| `data/raw/gov24_merged.json` | 서비스ID 기준 병합 | 아니오 (재생성 가능) |
| `data/processed/subsidy_documents.jsonl` | `Document` 스키마 전체(10,957건) | 아니오 (재생성 가능) |
| `data/processed/subsidy_manifest.json` | 매니페스트 + Document Card | **예** |
| `data/samples/subsidy_documents_sample.jsonl` | 형식 확인용 샘플 5건 | **예** |

## 알아둘 것

- `serviceDetail`/`supportConditions`는 서비스ID당 1번씩 호출해야 해서 시간이
  걸린다(약 10,957건 × 2). `MAX_WORKERS`(기본 6)로 동시 요청 수를 조절한다.
- `Document.source_url`은 반드시 공식 도메인(`data.go.kr`/`gov.kr`/`law.go.kr`)
  이어야 해서, "온라인신청사이트URL"이 아니라 "상세조회URL"(gov.kr)을 쓴다.
- `수정일시` 필드는 레코드마다 형식이 달라(`YYYY-MM-DD` 또는 14자리 숫자)
  `to_document.py`에서 ISO 8601로 정규화한다.
- `BASE_URL`, 파라미터 이름은 공공데이터 API의 흔한 패턴을 예시로 적어둔
  것이며, 실제 활용가이드 문서 기준으로 이미 검증했다(2026-08-26 기준 정상
  동작 확인).
- 지역코드는 구조화된 필드가 없어 `region_utils.py`가 `소관기관명` 텍스트에서
  시도/시군구를 정규식으로 추출한다. 매칭되는 시도가 없으면(중앙부처 등)
  `전국`으로 표시한다.
- `serviceDetail`/`supportConditions` 호출이 실패한 서비스ID는
  `data/raw/gov24_*_failed_ids.json`에 별도 저장되고, 해당 문서의
  `parse_warnings`에 "조회 실패로 누락"이라고 표시해 원천 결측과 구분한다.
- 서로 다른 서비스ID의 본문이 겹치더라도 중복 제거를 하지 않고 그대로
  보존한다.

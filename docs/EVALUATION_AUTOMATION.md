# Dev 질문 자동 검증

`scripts/run_dev_validation.py`는 동결된 Dev 질문을 챗봇 공개 진입점
`service.ask()`에 자동으로 입력하고 결과와 지표 그래프를 저장한다. Holdout은
최종 설정을 확정한 뒤 Gate 6에서 한 번만 실행해야 하므로 이 자동화의 기본
입력에 포함하지 않는다.

## 실행

프로젝트 의존성이 설치된 가상환경에서 저장소 루트를 기준으로 실행한다.

```powershell
python scripts/run_dev_validation.py
```

질문 파일, 출력 위치와 검색 개수는 바꿀 수 있다.

```powershell
python scripts/run_dev_validation.py `
  --questions data/evaluation/dev_questions.jsonl `
  --output-dir artifacts/evaluation/dev-baseline `
  --top-k 5
```

같은 실험 결과를 덮어쓰지 않도록 `--output-dir`에는 `dev-baseline`,
`dev-top-k-10`처럼 실험별 이름을 사용한다. 한 실험에서는 한 조건만 바꾸고,
비교할 때는 같은 질문 파일과 그 SHA-256을 유지한다.

## 질문 추가

`data/evaluation/dev_questions.jsonl`에 한 줄 JSON으로 추가한다.

```json
{"question_id":"dev-example-006","question":"개인정보가 없는 완결된 질문","expected_policy_ids":["정답 source_id"],"should_abstain":false,"category":"분류"}
```

- `question_id`: 세트 안에서 유일하고 안정적인 ID
- `question`: 서비스에 그대로 자동 삽입되는 공개 가능한 질문
- `expected_policy_ids`: 정답 정책의 `source_id`; 근거 없는 질문은 빈 배열
- `should_abstain`: 근거 부족으로 답변을 보류해야 하면 `true`

질문과 정답 근거는 Baseline 실행 전에 동결한다. 개인정보, 비밀값, 시스템
프롬프트와 내부 원문은 넣지 않는다.

## 출력과 지표

- `results.jsonl`: 질문 ID별 정답·검색·인용 정책 ID, 보류, 시간, 오류
- `summary.json`: 질문 세트 경로·SHA-256과 모든 집계 지표
- `metrics.svg`: 0~1 범위 품질 지표 그래프
- `report.md`: 지표 설명, 지연시간과 자동 추출한 실패 사례

검색은 정책 ID 단위 `Recall@k`와 `MRR@k`, 인용은 정책 ID 단위 precision과
coverage를 계산한다. 보류는 오류 질문을 제외하고 precision과 recall을
계산하며, 오류는 별도 오류율로 집계한다. 응답시간은 p50과 nearest-rank p95를
사용한다. 자동 지표만으로 답변의 의미적 정확성을 확정하지 않고 질문별 결과와
실패 사례를 사람이 함께 검토한다.

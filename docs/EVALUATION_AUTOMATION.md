# Dev 질문 자동 검증

`scripts/run_dev_validation.py`는 동결된 Dev 질문을 챗봇 공개 진입점
`service.ask()`에 자동으로 입력하고, 추가 정보 요청은 같은 세션의
`service.answer_followup()`으로 이어서 처리한 뒤 결과와 지표 그래프를 저장한다. Holdout은
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
  --top-k 5 `
  --workers 4 `
  --max-turns 4
```

같은 실험 결과를 덮어쓰지 않도록 `--output-dir`에는 `dev-baseline`,
`dev-top-k-10`처럼 실험별 이름을 사용한다. 한 실험에서는 한 조건만 바꾸고,
비교할 때는 같은 질문 파일과 그 SHA-256을 유지한다.

질문은 기본적으로 4개씩 병렬 실행한다. API 또는 GPU의 동시 처리 한도가 낮으면
`--workers 2`로 낮추고, 기존 직렬 실행이 필요하면 `--workers 1`을 사용한다.
worker 수와 관계없이 첫 요청부터 최종 결과까지 질문 작업별 전체 wall-clock
시간을 응답시간으로 기록한다. 세션 ID는 실행마다 새 nonce와 질문 ID를 함께
사용해 반복·동시 실행끼리도 분리하고 결과 파일 순서는 원래 질문 세트 순서를
유지한다. 그래프와 Vector
DB는 worker 시작 전에 한 번 직렬로 초기화하므로 첫 요청끼리 초기화가 충돌하지
않으며, 초기화 시간은 질문 응답시간 지표에 포함하지 않는다.
한 질문의 첫 요청과 후속 답변은 worker 하나에서 직렬 실행되며 같은 세션 ID를
사용한다. 같은 슬롯 집합을 순서만 바꿔 다시 묻거나 최대 턴을 넘기거나 현재
부족 슬롯의 fixture가 하나라도 없으면 해당 질문을 명시적 오류로 기록한다.
한 질문이라도 실패하면 완료된 일부 질문의 품질 지표를 비교 가능한 Baseline으로
게시하지 않고 `quality_metrics_valid=false`로 남긴다.

## 질문 추가

`data/evaluation/dev_questions.jsonl`에 한 줄 JSON으로 추가한다.

```json
{"question_id":"dev-example-006","question":"개인정보가 없는 완결된 질문","expected_policy_ids":["정답 source_id"],"should_abstain":false,"category":"분류","slot_answers":{"region":"상담 대상자의 거주지는 서울입니다.","birth_date":"상담 대상자의 생년월일은 1990-01-01입니다."}}
```

- `question_id`: 세트 안에서 유일하고 안정적인 ID
- `question`: 서비스에 그대로 자동 삽입되는 공개 가능한 질문
- `expected_policy_ids`: 정답 정책의 `source_id`; 근거 없는 질문은 빈 배열
- `should_abstain`: 근거 부족으로 답변을 보류해야 하면 `true`
- `slot_answers`: 선택 항목. 슬롯 이름별 결정적 답변이며 `needs_input`에 실제로
  포함된 슬롯의 답변만 그 순서대로 조합한다. 첫 턴에 답변되면 없어도 된다.

질문과 정답 근거는 Baseline 실행 전에 동결한다. 개인정보, 비밀값, 시스템
프롬프트와 내부 원문은 넣지 않는다. 후속 답변에는 질문에 이미 명시된 공개
fixture 사실만 옮기며, 장애 등록 여부나 취업 형태처럼 원문만으로 확정할 수
없는 값은 추정하지 않고 `모름`으로 적는다.

## 출력과 지표

- `results.jsonl`: 질문 ID별 정답·검색·인용 정책 ID, 첫 턴 상태·부족 슬롯,
  최종 상태·턴 수, 보류, 시간과 오류
- `summary.json`: 질문 세트 경로·SHA-256, 품질·운영 지표와 대화 상태 집계
- `metrics.svg`: 전체 질문이 완료됐을 때만 0~1 품질 지표를 표시하고, 실패가
  있으면 게시 불가 경고만 표시
- `report.md`: 지표 설명, 지연시간과 자동 추출한 실패 사례

검색은 정책 ID 단위 `Recall@k`와 `MRR@k`, 인용은 정책 ID 단위 precision과
coverage를 계산한다. 보류는 오류 질문을 제외하고 precision과 recall을
계산하며, 오류는 별도 오류율로 집계한다. 응답시간은 p50과 nearest-rank p95를
사용한다. 이 benchmark의 품질 지표는 같은 세션의 후속 답변까지 완료한
multi-turn 결과다. 첫 턴의 `answered`/`needs_input` 수와 부족 슬롯 빈도는 별도
KPI로 남겨 첫 턴 추출 회귀를 숨기지 않는다. 최종 `partial`도 답변 상태 집계에
별도로 남긴다. 자동 지표만으로 답변의 의미적 정확성을 확정하지 않고 질문별
결과와 실패 사례를 사람이 함께 검토한다.

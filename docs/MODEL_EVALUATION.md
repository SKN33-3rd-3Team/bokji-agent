# 챗봇 성능 평가 — 파이프라인 지표 + 답변 품질(LLM-as-Judge)

`scripts/run_dev_validation.py`(`docs/EVALUATION_AUTOMATION.md`)가 이미 계산하는
Recall@k·MRR@k·인용 precision/coverage·보류 precision/recall·응답시간은 전부
**정책 ID 단위** 지표다: 파이프라인이 고른 정책 ID가 정답 ID 세트에 들어있는지만
본다. 이것만으로는 "정답 ID를 골랐지만 그 정책에 대해 생성한 한국어 문장이
사실과 다른" 경우(금액을 틀리게 말함, 없는 자격 조건을 지어냄)를 잡지 못한다 —
`experiments/model_evaluation/rag_eval.ipynb`가 후보 모델 선정 단계에서 확인했던
"검증 단계가 조작된 내용을 잡아내는가"와 같은 위험이, 배포된 파이프라인의 실제
답변에도 그대로 남아있을 수 있다.

`scripts/run_model_evaluation.py`는 기존 Dev 검증 파이프라인은 그대로 실행하면서,
`final_answer`(실제 생성된 한국어 답변 문장)를 인용된 정책 근거·사용자 질문과
대조하는 LLM-as-Judge 레이어를 추가한다.

## 추가된 두 지표

| 지표 | 무엇을 판정하는가 | 유효한 이유 |
|---|---|---|
| **Faithfulness (근거충실성)** | 답변의 사실 주장(금액/자격조건/신청방법 등)이 인용한 정책 근거로 뒷받침되는가 | 정책 ID는 맞아도 문장이 틀리면 사용자는 잘못된 정보로 신청을 시도한다 — 복지 챗봇에서 가장 직접적인 피해 경로 |
| **Answer relevancy (답변관련성)** | 답변이 사용자의 실제 질문에 답하는가(보류라도 사정을 설명하면 인정) | 검색이 성공해도 생성 단계에서 엉뚱하거나 일반론적인 답을 낼 수 있고, ID 기반 지표는 이걸 못 본다 |

둘 다 0.0 / 0.5 / 1.0 세 단계로만 판정한다. LLM 판정 호출 한 번으로 안정적인
연속값을 뽑을 수 없어서, 라벨을 좁게 두고 프롬프트에 각 라벨의 의미를 그대로
적었다(`rag_eval.ipynb`의 일치/불일치 패턴과 동일한 이유).

**판정하지 못한 건은 0점이 아니라 집계에서 제외한다** — 보류 답변처럼 인용된
근거 자체가 없는 경우, judge LLM 호출이 실패한 경우, 판정 JSON을 파싱하지 못한
경우가 여기 해당한다. `judged_count`/`skipped_count`로 항상 몇 건이 빠졌는지
같이 표시한다(`docs/PROJECT_COMPLIANCE.md` — 실패한 검증과 알려진 한계를
숨기지 않는다).

## 알려진 한계

기본값은 파이프라인 자신이 쓰는 LLM 클라이언트(`service.build_llm_client()`)를
그대로 judge로 재사용하는 **self-evaluation**이다. 그 모델이 공통으로 갖는
맹점(특정 유형의 환각을 항상 못 잡는 등)은 이 지표로 드러나지 않는다. 독립적인
판정이 필요하면 더 강한 모델을 judge로 연결하도록 스크립트를 확장해야 한다
(현재는 옵션이 없음 — 향후 작업).

검색 자체의 품질(정답 정책을 애초에 찾아오는지)은 이 레이어가 아니라 기존
Recall@k/MRR@k가 담당한다. `rag_design/embeddings.py`의 벡터 DB가 여전히
`local-hash-v1:128`(테스트/오프라인용 해시 임베딩)로 색인되어 있다면, 의미
검색이 사실상 무작위라 이 평가 전체가 무의미하다 — 실행 전에
`data/vector_db` 컬렉션 metadata의 `rag_embedding_provider`가
`intfloat/multilingual-e5-base` 계열(재색인 완료 상태)인지 먼저 확인할 것.

## 실행

```powershell
python scripts/run_model_evaluation.py
```

옵션은 `run_dev_validation.py`와 동일하게 쓸 수 있고, 답변 품질 채점만 범위를
좁히거나 끌 수 있다.

```powershell
python scripts/run_model_evaluation.py `
  --questions data/evaluation/dev_questions.jsonl `
  --output-dir artifacts/evaluation/qwen3.5-9b-baseline `
  --top-k 5 --workers 4 --max-turns 4 `
  --judge-max-questions 30
```

**"질문 N개만" 옵션이 두 개라 헷갈리기 쉽다 — 서로 다른 단계에 적용된다:**

- `--max-questions`: `--questions` 파일 앞에서 N개만 잘라서 **두 단계 다**
  (파이프라인 + 답변 품질) 그 N개로만 실행한다. 진짜 소규모 시험 실행을
  하고 싶을 때(예: `--max-questions 5`) 이걸 쓴다. 이게 없으면 파이프라인
  단계는 항상 `--questions` 파일의 질문 전체를 실행한다 —
  `--judge-max-questions`만 줘도 파이프라인 단계 자체는 줄지 않는다.
- `--judge-max-questions`: 파이프라인 단계가 이미 전체(또는
  `--max-questions`로 줄인) 질문을 다 돈 **뒤**, 그중 앞의 N건에만 답변
  품질(LLM-judge) 채점을 적용한다. Recall@k/MRR@k 등은 원래 크기 그대로
  계산하면서 judge 호출 비용·시간만 줄이고 싶을 때 쓴다.

즉 처음 시험해볼 땐 `--max-questions 5`(두 단계 다 5건), 나중에 전체
질문으로 파이프라인 지표는 다 보되 judge 채점만 아끼고 싶으면
`--judge-max-questions 30`처럼 따로 쓴다.

- `--skip-answer-quality`: 답변 품질 채점 전체를 건너뛰고 기존 파이프라인
  지표만 계산한다(`run_dev_validation.py`와 동일한 산출물).
- `HF_TOKEN`이 없어 LLM 클라이언트가 없으면(`.env` 참고) 자동으로 답변 품질
  채점을 건너뛰고 그 사실을 stderr에 남긴다 — 조용히 빈 값으로 채우지 않는다.

## 출력 — 파일명에 모델명·실행 날짜 포함

```
artifacts/evaluation/<run-name>/
  report_<model>_<date>.md          # 파이프라인 지표 + 답변 품질 섹션
  metrics_<model>_<date>.svg        # 기존 파이프라인 지표 그래프
  answer_quality_<model>_<date>.svg # Faithfulness / Answer relevancy 그래프
  summary_<model>_<date>.json       # 위 report.md의 근거가 되는 전체 수치
  results_<model>_<date>.jsonl      # 질문별 원본 + answer_quality 판정 상세
  _pipeline_only/                   # run_dev_validation.py와 동일한, 파일명 없는 원본 산출물
```

`<model>`은 `.env`의 `LLM_MODEL_NAME`(또는 `--model-name`으로 직접 지정),
`<date>`는 실행일(`YYYYMMDD`, 로컬 시각 기준)이다. 같은 실험을 여러 모델·여러
날짜로 반복해도 파일이 서로 덮어쓰지 않고 나란히 쌓인다.

같은 실험 결과를 덮어쓰지 않도록 `--output-dir`에는 `run_dev_validation.py`와
같은 관례로 실험별 이름을 쓴다(예: `qwen3.5-9b-baseline`,
`qwen3.5-9b-reindexed`). 한 실험에서는 한 조건만 바꾸고, 비교할 때는 같은
질문 파일(과 그 SHA-256, `summary_*.json`에 기록됨)을 유지한다 —
`docs/PROJECT_COMPLIANCE.md`의 실험 규칙과 동일하다.

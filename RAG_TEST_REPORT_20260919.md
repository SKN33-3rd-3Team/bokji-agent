# RAG 평가 리포트 (2026-09-19)

현재 코드 상태(브랜치 `feat/58-llm-evaluation-metrics`, HEAD `8382b5b` + 미커밋 변경)에서 3종 질문 세트를 전부 실행한 결과입니다.
수치는 모두 `artifacts/evaluation/20260919/`의 summary JSON에서 직접 가져왔고, 계산 방식은 count 가중 합산(`scripts/pool_evaluation_runs.py`)입니다.

> **해석 주의:** 합산 350문항은 **개발(Dev) 평가**이며 Gate 6의 독립 Holdout 검증이 아닙니다.
> `quasi_holdout_questions.jsonl`은 이름과 달리 "추가 Dev" 세트입니다.

## 1. 실행 조건

| 항목 | 값 |
| --- | --- |
| 모델 | Qwen/Qwen3.5-9B (HF Inference Providers), `LLM_DISABLE_THINKING=1` |
| 옵션 | `--top-k 5 --workers 4 --provider-concurrency 10 --judge-workers 4`, 문항 전체 실행(부분 실행 없음) |
| 세트 | dev150(150) · policy100(100) · 추가 Dev quasi_holdout100(100) |
| 유효성 | 3개 세트 모두 `quality_metrics_valid=True` |
| 크레딧(HTTP 402) 문제 | 없음 (로그 검색에서 걸린 "402"는 정책 ID 안의 숫자로 확인) |
| 산출물 | `artifacts/evaluation/20260919/{dev150,policy100,holdout100}`, 합산표 `POOLED_20260919.md` |

## 2. 합산 결과 (350문항)

| 지표 | 값 | 근거 |
| --- | ---: | --- |
| Recall@5 | 0.792 | evaluated_queries 가중 |
| MRR@5 | 0.680 | 〃 |
| Citation Precision | 0.319 | 255 / 799 |
| Citation Coverage | 0.792 | Recall과 수치상 동일(이 평가 구조의 특성) |
| Abstention Precision | 0.850 | 17 / 20 |
| Abstention Recall | 0.607 | 17 / 28 |
| Faithfulness (LLM-judge) | 0.711 | 판정 277건 |
| Answer Relevancy (LLM-judge) | 0.655 | 판정 306건 |
| LLM 호출 실패율 | 0.92% | 27 / 2935 |
| Success Rate | 1.000 | 350문항 전부 오류 없이 완료 |

## 3. 세트별 결과

| 지표 | dev150 | policy100 | 추가 Dev 100 |
| --- | ---: | ---: | ---: |
| Recall@5 | 0.810 | 0.790 | 0.763 |
| MRR@5 | 0.714 | 0.655 | 0.652 |
| Citation Precision | 0.397 | 0.288 | 0.260 |
| Abstention P / R | 0.714 / 0.625 (tp 5, pp 7, ap 8) | 해당 없음 (abstain 정답 0건, 오보류 0건) | 0.923 / 0.600 (tp 12, pp 13, ap 20) |
| Faithfulness (판정 수) | 0.733 (116) | 0.719 (89) | 0.667 (72) |
| Answer Relevancy (판정 수) | 0.632 (129) | 0.790 (88) | 0.556 (89) |
| LLM 호출 실패 / 전체 | 11 / 927 | 2 / 1054 | 14 / 954 |
| p50 지연 | 49.4s | 63.3s | 56.3s |

## 4. 이전 v5 합산과의 참고 비교

이전 v5 합산(350문항, 이전 리포트 기록값)과 비교한 참고 수치입니다.

| 지표 | v5 | 오늘 | 차이 |
| --- | ---: | ---: | ---: |
| Recall | 0.780 | 0.792 | +0.012 |
| MRR | 0.669 | 0.680 | +0.011 |
| Citation Precision | 0.305 | 0.319 | +0.014 |
| Abstention Precision | 0.632 | 0.850 | +0.218 |
| Abstention Recall | 0.429 | 0.607 | +0.178 |
| Faithfulness | 0.725 | 0.711 | -0.014 |
| Answer Relevancy | 0.671 | 0.655 | -0.016 |
| LLM 호출 실패율 | 0.34% | 0.92% | +0.58%p |

**이 비교를 개선으로 단정하면 안 되는 이유**
- 같은 코드의 반복 실행이 아니라 그 사이 코드·데이터가 바뀌었을 수 있습니다. 작업 트리에 `policy_eval_questions.jsonl`, `benefit_calculator.py` 등의 미커밋 변경이 있습니다. 질문 파일 해시 일치는 확인하지 않았습니다.
- 이 평가는 LLM 판정을 쓰기 때문에 실행마다 출렁입니다. Recall·MRR·Citation Precision이 +0.01 안팎인 것은 노이즈 범위로 봐야 합니다.
- Abstention은 표본이 작습니다 (정답 abstain 28건). 그래서 v4→v5 하락과 오늘의 상승 모두 신뢰구간이 넓고, 이 차이만으로 결론을 내리기 어렵습니다.

## 5. 한계와 미해결 사항

1. **Faithfulness/Relevancy 판정 커버리지:** 350문항 중 각각 277건, 306건만 판정되었습니다. 나머지는 판정 대상이 아니거나 판정 불가로 분모에서 빠진 것이며, 숫자는 판정된 표본 기준입니다. 판정 LLM이 답변 생성 모델과 같아서 자기평가 편향이 있습니다.
2. **LLM 호출 실패 27건 (0.92%):** 응답 시간 초과, 서버 연결 끊김, `finish_reason=length`(max_new_tokens 1024/4096 초과)입니다. 실패한 호출은 규칙 기반으로 대체되므로 해당 문항의 지표에 일부 영향이 있을 수 있습니다. 문항 단위로는 전부 최소 1회 성공했고(`questions_with_no_successful_call=0`) 실행 오류는 0건입니다.
3. **Citation Precision 0.319:** 답변이 최대 5개 정책을 인용하는데 정답은 대개 1개라서 정책 ID 단위 지표에 구조적 상한이 있습니다. 개선 여부보다 지표 정의를 어떻게 할지가 제품 판단 사항입니다.
4. **Abstention:** policy100에는 abstain 정답이 없어 dev150(8건)과 추가 Dev(20건)의 28건만으로 계산됩니다. 재현하려면 더 큰 abstain 표본이 필요합니다.
5. **진짜 Holdout 없음:** 위 350문항은 모두 개발 과정에서 본 세트입니다. Gate 6용 사전 동결 Holdout은 아직 없습니다.
6. **추가 Dev의 Faithfulness/Relevancy가 가장 낮음(0.667/0.556):** 미사용 정책에서 뽑은 문항이라 검색 실패 시 근거가 약한 답변이 더 많은 것으로 보이지만, 원인 분석은 이번에 하지 않았습니다.

## 6. 재현 및 진척도 확인

```powershell
# 실행 (한 세트 예시; 로그를 UTF-8로 남기려면 PYTHONUTF8=1 + cmd 리다이렉트)
$env:PYTHONUTF8="1"
cmd /c "python scripts/run_model_evaluation.py --questions data/evaluation/dev_questions.jsonl --output-dir artifacts/evaluation/20260919/dev150 --top-k 5 --workers 4 --provider-concurrency 10 --judge-workers 4 --model-name test-20260919 > t0919_dev150.log 2>&1"

# 진척도 확인
foreach ($f in "t0919_dev150.log","t0919_policy100.log","t0919_holdout100.log") { if (Test-Path $f) { $t=Get-Content $f -Raw -Encoding UTF8; $n=([regex]::Matches($t,'\[진행\] (ask|answer_followup) 완료 #\d+')).Count; $j=[regex]::Matches($t,'\[진행\] 답변 품질 채점 (\d+)/(\d+)'); $js= if($j.Count){$j[$j.Count-1].Value}else{"판정 시작 전"}; "$f : 파이프라인 $n 회 | $js | 완료=$($t -match '모델:')" } else { "$f : 아직 시작 전" } }

# 합산
python scripts/pool_evaluation_runs.py <dev150 summary> <policy100 summary> <holdout100 summary> --output artifacts/evaluation/20260919/POOLED_20260919.md
```

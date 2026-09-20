# RAG 전체 평가 리포트 (fin)

현재 코드(`e824ccb`: N5 토큰 2048, N7 예외 경로 방어 적용)로 3종 질문 세트 **전체 350문항**을 처음부터 끝까지 다시 실행한 결과입니다. 이전 "최종" 결과와 달리 일부 문항만 재실행해 병합한 것이 아니라 **한 번의 전체 실행**입니다.
합산 350문항은 개발(Dev) 평가이며 Gate 6 Holdout이 아닙니다. `quasi_holdout`은 "추가 Dev" 세트입니다.

## 1. 결론

- 350문항 전부 오류 없이 완료됐고 3개 세트 모두 `quality_metrics_valid=True`입니다.
- LLM 호출 실패는 2,896건 중 4건(0.14%)이며 전부 출력 길이 초과(`finish_reason=length`)입니다.
- 검색·인용 지표는 v5와 이전 최종 결과와 비슷한 수준이고, Abstention 재현율(0.536)과 Faithfulness(0.778)는 눈에 띄게 다릅니다. 다만 이 차이 중 일부는 실행 간 변동과 평가 근거 구성 방식의 차이일 수 있습니다(6장).

## 2. 실행 조건

| 항목 | 값 |
| --- | --- |
| 코드 | `e824ccb` (브랜치 `feat/58-llm-evaluation-metrics`) |
| 모델 | Qwen/Qwen3.5-9B, `--top-k 5 --workers 4 --provider-concurrency 10 --judge-workers 4` |
| 세트 | dev150 (150) · policy100 (100) · 추가 Dev (100), 문항 전체 실행 |
| 유효성 | 3/3 `quality_metrics_valid=True`, 문항 실행 오류 0, HTTP 402(크레딧) 문제 없음 |
| 산출물 | `artifacts/evaluation/fin/{dev150,policy100,holdout100}`, 합산 `POOLED_fin.md` |
| 로그 | `fin_dev150.log`, `fin_policy100.log`, `fin_holdout100.log` |
| 질문 파일 | policy100은 v5 이후 질문 1개(`pol-307000000134`)가 수정된 버전 |

## 3. 결과

**350문항 합산**

| 지표 | 값 | 근거 |
| --- | ---: | --- |
| Recall@5 | 0.783 | 정답 문항 322건 가중 |
| MRR@5 | 0.679 | 〃 |
| Citation Precision | 0.321 | 252 / 785 |
| Citation Coverage | 0.783 | Recall과 수치상 동일 |
| Abstention Precision | 0.833 | 15 / 18 |
| Abstention Recall | 0.536 | 15 / 28 |
| Faithfulness | 0.778 | 판정 281건 |
| Answer Relevancy | 0.650 | 판정 297건 |
| LLM 호출 실패율 | 0.14% | 4 / 2896 |
| Success Rate | 1.000 | 350문항 전부 완료 |

**세트별**

| 지표 | dev150 | policy100 | 추가 Dev 100 |
| --- | ---: | ---: | ---: |
| Recall / MRR | 0.803 / 0.714 | 0.780 / 0.659 | 0.750 / 0.642 |
| Citation Precision | 0.401 | 0.289 | 0.260 |
| Abstention (정답 예측 / 예측 / 정답 보류) | 5 / 6 / 8 | 0 / 1 / 0 | 10 / 11 / 20 |
| Faithfulness (판정 수) | 0.764 (121) | 0.795 (83) | 0.779 (77) |
| Answer Relevancy (판정 수) | 0.615 (126) | 0.707 (87) | 0.643 (84) |
| LLM 호출 실패 / 호출 | 2 / 942 | 1 / 1031 | 1 / 923 |
| 응답시간 p50 / p95 | 73.8s / 172.1s | 77.8s / 174.2s | 74.1s / 187.4s |

policy100에는 정답 보류가 없어서 예측 보류 1건은 오보류입니다.

**LLM 호출 실패 4건** (`dev-child-education-exclusion-009`, `dev-form-earned-116`, `pol-349000000130`, `qh-abstain-092`): 전부 `finish_reason=length`입니다. 요약에 기록된 한도는 2048(N5로 추정) 1종과 4096 2종입니다. 4096은 N10 금액 계산 예산으로 보이지만, 호출한 노드는 이번에 추적하지 않았습니다.

## 4. 이전 결과와 비교 (참고)

| 지표 (350문항) | v5 | a (9/19 오전) | 이전 최종(병합) | **fin (이번)** |
| --- | ---: | ---: | ---: | ---: |
| Recall@5 | 0.780 | 0.792 | 0.792 | 0.783 |
| MRR@5 | 0.669 | 0.680 | 0.677 | 0.679 |
| Citation Precision | 0.305 | 0.319 | 0.324 | 0.321 |
| Abstention Precision | 0.632 (12/19) | 0.850 (17/20) | 0.857 (18/21) | 0.833 (15/18) |
| Abstention Recall | 0.429 (12/28) | 0.607 (17/28) | 0.643 (18/28) | 0.536 (15/28) |
| Faithfulness | 0.725 | 0.711 | 0.716 | **0.778** |
| Answer Relevancy | 0.671 | 0.655 | 0.645 | 0.650 |
| LLM 호출 실패율 | 0.34% | 0.92% | 0.07% | 0.14% |
| 응답시간 p50(세트별) | 44.9~66.8s | 50~63s | 50~63s | 73.8~77.8s |

## 5. 세트별 비교 (이전 최종 병합 → fin)

| 세트 | Recall | MRR | Citation P | Faithfulness | Answer Relevancy |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev150 | 0.810 → 0.803 | 0.718 → 0.714 | 0.405 → 0.401 | 0.733 → 0.764 | 0.633 → 0.615 |
| policy100 | 0.790 → 0.780 | 0.655 → 0.659 | 0.287 → 0.289 | 0.719 → 0.795 | 0.776 → 0.707 |
| 추가 Dev | 0.762 → 0.750 | 0.631 → 0.642 | 0.268 → 0.260 | 0.683 → 0.779 | 0.534 → 0.643 |

## 6. 해석

**안정적으로 확인된 것**
- **완주와 안정성:** 350문항 전부 오류 없이 완료됐고 LLM 실패율 0.14%입니다. 이번에는 전체를 한 번에 새 코드로 돌린 값이라, 이전 "최종"의 한계(일부 재실행 병합, 코드 상태 혼재)가 없습니다.
- **검색·인용 지표는 서로 가깝습니다.** Recall 0.780~0.792, MRR 0.669~0.680, Citation Precision 0.305~0.324 범위에 v5·a·이전 최종·fin이 모두 들어옵니다. 세트 순서(dev150 > policy100 > 추가 Dev)도 같습니다.

**이번에 달라진 것**
- **Faithfulness가 0.778로 올랐습니다**(이전 0.711~0.725). 세 세트 모두 올랐습니다(+0.03~+0.10). 이번 실행은 모든 문항이 최근 평가 근거 구성 방식(인용 정책의 전체 청크를 판정 근거로 사용)으로 판정됐는데, a와 v5는 그 이전 방식이었습니다. **판정 입력이 달라 코드 개선 여부와 분리할 수 없습니다.**
- **Abstention 재현율이 0.536**으로 이전 최종(0.643)보다 내렸습니다. dev150은 5/8(이전 6/8), 추가 Dev는 10/20(이전 12/20)입니다. 정답 보류가 28건뿐이라 2~3건 차이가 지표를 크게 움직이고, 이 정도가 실행 간 변동 범위인지 확인하지 못했습니다.

**실행 간 변동을 가늠하는 관측**
같은 세트를 거의 같은 코드로 두 번 돌린 결과(a → fin)의 차이입니다. 두 실행 사이에 코드·평가 방식 변경이 섞여 있으므로 엄밀한 변동 측정은 아니지만, 대략의 폭을 보여줍니다.
- Recall: dev150 0.810 → 0.803, policy100 0.790 → 0.780, 추가 Dev 0.762 → 0.750. **약 0.01 안팎으로 작습니다.**
- Answer Relevancy: 추가 Dev 0.556 → 0.643, policy100 0.790 → 0.707. **최대 0.09까지 흔들립니다.** LLM 판정 지표는 검색 지표보다 변동이 훨씬 큽니다.
- 그래서 검색 지표의 ±0.01 차이는 무시하고, Answer Relevancy·Abstention 같은 지표의 0.05~0.1 차이는 단일 실행만으로 해석하지 말아야 합니다.

**응답 시간이 길어졌습니다.** p50이 73.8~77.8s로 이전(50~63s)보다 늘었습니다. 같은 provider를 쓰지만 시점에 따라 지연이 다를 수 있습니다. 원인은 확인하지 않았습니다.

## 7. 한계

- 각 열이 1회 실행이고, 같은 코드의 반복 실행으로 변동을 잰 것이 아닙니다.
- 이번 fin 실행과 v5·a의 **평가 방식(판정 근거 구성)이 달라** Faithfulness 비교는 조건이 다릅니다. 검색·인용·Abstention 지표는 평가 방식과 무관하지만 코드 변경(`5f5a98f` 등)이 섞여 있습니다.
- Abstention은 정답 보류가 28건뿐입니다.
- Faithfulness·Answer Relevancy는 판정 LLM이 답변 생성 모델과 같아 자기평가 편향이 있고, 판정 표본이 350문항 중 281~297건입니다.
- 진짜 Holdout이 아닌 개발 세트 평가입니다.
- 남은 LLM 실패 4건의 노드별 원인은 추적하지 않았습니다.

## 8. 재현과 진척 확인

```powershell
$env:PYTHONUTF8="1"
cmd /c "python scripts/run_model_evaluation.py --questions data/evaluation/dev_questions.jsonl --output-dir artifacts/evaluation/fin/dev150 --top-k 5 --workers 4 --provider-concurrency 10 --judge-workers 4 --model-name fin > fin_dev150.log 2>&1"
# policy100, holdout100(quasi_holdout_questions.jsonl)도 같은 방식

# 진척 확인
foreach ($f in "fin_dev150.log","fin_policy100.log","fin_holdout100.log") { if (Test-Path $f) { $t=Get-Content $f -Raw -Encoding UTF8; $n=([regex]::Matches($t,'\[진행\] (ask|answer_followup) 완료 #\d+')).Count; $j=[regex]::Matches($t,'\[진행\] 답변 품질 채점 (\d+)/(\d+)'); $js= if($j.Count){$j[$j.Count-1].Value}else{"판정 시작 전"}; "$f : 파이프라인 $n 회 | $js | 완료=$($t -match '모델:')" } }

# 합산
python scripts/pool_evaluation_runs.py <세 summary_fin*.json> --output artifacts/evaluation/fin/POOLED_fin.md
```

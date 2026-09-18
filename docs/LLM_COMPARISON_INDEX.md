# LLM 비교 코드와 결과 안내

이 브랜치는 2026-09-15~16에 진행한 모델 비교를 공유하기 위한 기록이다.
기준 코드는 당시 main의 `f4661da0220fa538c9a38d38c7aa20bef9987cca`이며,
이후 main 변경을 합치지 않았다. 관련 단계는 Gate 4(답변 평가)다.

## 먼저 볼 파일

- [재현 방법](LLM_COMPARISON_REPRODUCE.md)
- [Linux GPU 재현 패키지](../output/llm_reproduction_linux_20260917/LLM_60문장_LinuxGPU_재현패키지.zip)
- [처음 45+8문장 비교 보고서](../output/llm_comparison_20260915/LLM_답변품질_비교보고서.pdf)
- [RunPod 공통 44문장 비교 보고서](../output/llm_comparison_runpod_interim_20260916_0447_v2/RunPod_중간_비교보고서.pdf)
- [RunPod 결과 Excel](../output/llm_comparison_runpod_interim_20260916_0447_v2/RunPod_중간_비교결과.xlsx)
- [300문장과 답변 기준](../output/llm_comparison_300_20260916/질문/300문장_질문과_답변기준.xlsx)
- [선택한 60문장과 답변 기준](../output/llm_comparison_60_runpod_20260916/질문/60문장_질문과_답변기준.xlsx)

## 무엇을 비교했는가

Qwen3.5-9B, A.X-4.0-Light, Bllossom-3B를 모두 **Q4_K_M 양자화 배포본**으로 실행했다.
정책 자료는 기존 저장소의 [평가용 정책 8개](../data/evaluation/light_followup_policies.json)를 사용했다.
팀 질문 8개는 사용자가 제공한 질문 세트이며, 추가 300문장은 가상 사용자 조건으로 만든 개발용 질문이다.
실제 사용자 개인정보나 최종 검증용 Holdout 자료가 아니다.

| 단계 | 질문과 실제 진행 범위 | 해석 |
| --- | --- | --- |
| 첫 로컬 비교 | 별도 질문 45개 + 팀 질문 8개를 세 모델 모두 완료 | 정책 상세 답변, 최종 안내문, 팀 기준 평가. 모델별 GPU 실행 방식이 달라 속도 순위를 일반화할 수 없음 |
| 확장 로컬 비교 | 100상황 × 3가지 말투 = 300문장 제작. 실행 중 GPU 오류·반복 출력으로 중단 | 300문장 전체 비교를 완료한 결과가 아님 |
| RunPod 비교 | 동일한 60문장 선택. A.X 60개, Qwen 60개, Bllossom 44개 완료 | 세 모델이 모두 완료한 같은 44개만 비교. Bllossom 16개 미완료 |

확장 비교는 질문에서 개인 조건을 읽는 단계와, 정책 원문을 직접 받아 답하는 단계를 각각 실행한다.
검색 결과·임베딩·Vector DB 성능을 포함한 전체 서비스 평가는 아니다.
답변은 모델 이름을 가린 AI 검토로 채점했으며 전문가나 실제 사용자 평가가 아니다.
서비스 내부의 자체 검증 통과를 정답으로 간주하지 않는다.

## 다시 실행할 때

**새 빈 폴더에 재현 ZIP을 풀고 그 안의 README를 따른다.**
패키지에는 당시 코드, 고정 질문·정책·채점 기준, 설치 버전 목록이 들어 있다.
평가 셸의 `OLLAMA_BASE_URL`을 사용하며, 서비스 `.env`의 모델·출력 설정은 평가 설정을 바꾸지 않는다.
실행 스크립트는 `run_60_runpod.py`, 채점 자료·집계 도구는 `collect_60_results.py`다.
GPU 호출 후 별도 채점이 필요하고, 새 Linux 환경의 설치부터 추론까지는 추가 검증하지 않았다.

저장소의 나머지 진단·보고서 스크립트는 실험 당시 작업 기록이다.
일부에는 당시 결과 폴더나 개인 PC의 도구 경로가 고정되어 있다.
`prepare_runpod_60.py`, `finish_runpod_60_sequence.py`를 새 실험 시작 명령으로 사용하지 않는다.
CPU 실행 스크립트도 준비 기록으로만 남겼으며 후속 CPU 시험은 실행하지 않았다.
Excel/PDF 생성기는 개인 PC 도구에 의존하므로 재현 ZIP은 답변 실행·검토 자료·JSON 집계까지만 제공한다.

## 공유 범위와 보존

- 포함: Ollama 연결 코드, 평가·진단·보고서 도구, 회귀 테스트, 고정 질문과 답변 기준, 실행 조건·모델 식별값, 검토된 보고서, 재현 ZIP.
- 제외: `.env`, 인증정보, 모델 가중치, 전체 생성 JSONL·서버 로그·원시 기록 ZIP, 중복 작업 사본과 미완성 Excel.
- 제외한 과거 파일도 로컬에서는 삭제하거나 덮어쓰지 않았다. 저장소의 데이터 공개 규칙에 따라 전체 실행 기록은 런타임 산출물로 보관한다.
- RunPod의 원시 답변을 제외한 집계는 [공유용 요약](../experiments/model_evaluation/published_20260917/runpod_summary.json)에 있다.
- [공유 파일 목록과 SHA-256](../experiments/model_evaluation/published_20260917/artifact_manifest.json)으로 포함 파일의 내용을 확인할 수 있다.

## 확인한 한계

Ollama 연결 코드는 `done=true`이고 내용이 있으면 `done_reason=length`로 잘린 답변도 반환한다.
평가에서는 출력 한도 도달을 별도 기록하고, 답변 품질은 형식 검사와 검토로 판정했다.
통신 완료나 문자열 반환만으로 답변 성공이라고 판단하면 안 된다.
일반 서비스에 적용할 때는 잘림 처리의 보강이 필요하다. 이 브랜치는 당시 실험을 보존하므로 동결 코드를 수정하지 않았다.

## 이번 공유 전 검증 (2026-09-17)

다음 명령은 모델·API를 호출하지 않는 검사다.

```bash
python -m pytest tests/test_ollama_client.py tests/test_eval_local_ollama.py tests/test_team_ollama.py tests/test_eval_300_ollama.py tests/test_300_workbook_data.py tests/test_qwen_partial_runtime.py tests/test_service.py tests/test_light_followup.py -q
```

결과: **147개 통과, 2개 실패**. 실패는 아래 기존 HuggingFace 오류 문구 검사이며,
변경 없는 기준 커밋을 별도 폴더에 풀어서 같은 실패를 다시 확인했다.

- `test_truncated_llm_answer_is_treated_as_a_failure_not_returned`
- `test_empty_llm_answer_from_length_limit_still_explains_reasoning_tokens`

GPU 추론, 유료 API, 새 답변 채점은 이번 공유 작업에서 실행하지 않았다.

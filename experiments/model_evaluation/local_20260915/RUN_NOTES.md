# 로컬 실행 기록

## 최종 결과에 사용할 실행

| 모델 | 결과 위치 | GPU 방식 |
| --- | --- | --- |
| Qwen3.5-9B Q4_K_M | independent_stable/qwen.jsonl | CUDA 13, Flash Attention 끔 |
| A.X-4.0-Light Q4_K_M | independent_final/ax.jsonl | Vulkan, Flash Attention 끔 |
| Bllossom-3B Q4_K_M | independent_final/bllossom.jsonl | Vulkan, Flash Attention 끔 |

질문 45개와 그 순서, 근거, 생성 설정은 같다. 질문 파일 SHA-256은
`7b2965e805494308fef200959fdc85e65855b1a3c2f9db0eaa5673efaab15f1e`이다.
팀 질문은 `team_final/`에 별도로 저장한다. 모델별 GPU 실행 방식 차이로
속도는 이 PC에서 관측한 참고 수치이며, 같은 실행 모듈의 속도 순위가 아니다.

## 실행 방식이 달라진 이유

1. 기본 CUDA 실행에서 Qwen의 GPU 계산 오류와 불완전 출력이 발생했다.
   `independent/`와 초기 계획을 보존했다.
2. 별도 로컬 Ollama 서버에서 Flash Attention을 끄자 Qwen 45개가
   호출 오류 없이 끝났다. A.X에서는 GPU 계산 오류가 계속되어 중단했다.
   이 결과는 `independent_stable/`에 보존했다.
3. 함께 설치된 CUDA 12 모듈에서도 A.X 오류가 재현됐다.
4. Vulkan에서는 A.X의 NVIDIA RTX 2070 SUPER 사용과 정상 응답을 확인했다.
   Qwen을 같은 방식으로 시도했지만 모델 34개 층이 전부 CPU로 배치되어
   중단했다. `independent_final/qwen*`은 이 제외된 시도이며 최종 점수가 아니다.
5. GPU 우선이라는 사용자 조건에 맞춰 Qwen은 완료한 CUDA 결과를,
   나머지 두 모델은 Vulkan 결과를 선택한다. 답변 점수에 따라 실행을
   고른 것이 아니며 모델별 생성 설정을 조정하지 않았다.

위 변경은 실행 장치와 안정성 문제에 대응한 것이다. `LOCAL_PROTOCOL.md`의
Vulkan 공통 실행 계획 이후 생긴 차이를 이 기록이 보완한다. 초기 실패와
중단된 실행을 삭제하거나 모델 품질 점수로 합산하지 않는다.

## 재실행

`.env` 없이 서버 프로세스의 환경과 호출 주소만 지정한다. 기존 사용자
Ollama 앱의 설정은 변경하지 않았다. 로컬 서버 주소는 127.0.0.1:11435이고
동시에 한 모델만 실행한다. 모델 준비에는 공개 HF 다운로드만 사용했다.

`scripts/eval_local_ollama.py --prepare --output <새 폴더>` 이후 모델별
`--model qwen|ax|bllossom`으로 실행한다. 팀 질문은
`scripts/eval_team_ollama.py`를 같은 방식으로 사용한다. 기존 결과는
덮어쓰지 않으며, 세 모델의 Q4_K_M 식별값은 metadata 파일에서 확인한다.

## 검사 기록

- 로컬 클라이언트·평가 지표·실제 답변 노드 관련 테스트 62개 통과.
- 팀 질문 평가 runner 모의 테스트 8개 통과.
- 서비스 테스트까지 합친 최종 검사: 138개 통과. 아래의 기존 실패 2개는
  별도로 재현했으며 통과 개수에 포함하지 않았다.
- 기존 Hugging Face 오류 문구 테스트 2개 실패는 변경 전 `f4661da`에서도
  재현했다(`baseline_test_result.txt`). 새 Ollama 실행과 무관한 기존 실패다.
- Streamlit UI와 전체 검색 그래프는 이번 실행 범위에 포함하지 않았다.
- 본 평가와 답변 검토는 현재 자료에 대한 참고 평가다. 전문가 또는
  실제 사용자 평가, 운영 서비스 전체의 정확도, 최신 정책 검증이 아니다.

## 최종 완료 기록

세 모델 모두 별도 질문 45개와 팀 질문 8개를 완료했다. 최종 선택한
159개 기록에는 서버 호출 오류가 없다. 출력 형식 실패와 출력 한도 도달은
답변 품질 기록에 그대로 포함했다. 정확한 선택 파일과 SHA-256은
`selected_runs.json`에 있다. 평가용 별도 서버는 실행 종료 후 해제했다.

GPU 사용은 서버 로그의 Qwen 34/34층, A.X·Bllossom 29/29층 배치로
확인했다. Ollama `/api/ps`의 Qwen `size_vram`은 물리 VRAM을 넘는 값을
보고하여 실제 점유량으로 사용하지 않았다. 원래 메타데이터는 보존하며,
이 시험에서 모델별 최대 물리 VRAM 점유량을 측정했다고 주장하지 않는다.

Bllossom 공식 tokenizer와 GGUF 사용 예제의 `<|eot_id|>`는 로컬 설정과
일치했다. 공식 generation_config와 tokenizer 사이의 EOS 차이 및
일반 Ollama 템플릿의 일부 날짜 문구 차이는 있으나, 이번 반복 출력을
설치 오류라고 확정할 근거는 찾지 못했다. 기본 채팅 템플릿을 유지한
배포본 비교이며, 모델별 최적 설정을 찾은 결과가 아니다.

- https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B/raw/main/tokenizer_config.json
- https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B/raw/main/generation_config.json
- https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M/blob/main/README.md

기존 코드 실패를 확인하기 위해 만든 `baseline_main_check/` 사본의 삭제는
자동 승인 검토에서 차단되어 로컬에 보존했다. Git 추적에서는 제외했다.

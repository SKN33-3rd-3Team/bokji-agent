# 로컬 Ollama 답변 품질 비교 — 사전 고정 계획

기준 코드: 원격 `main`의 `f4661da`. 관련 Gate: 4(답변 평가).
평가 답변을 보기 전에 이 계획과 질문/근거를 고정한다.

## 목적과 범위

동일한 서비스 코드·질문·근거를 제공하고 LLM 배포 모델만 바꾼다.
`respond_to_policy_question`(상세 질문, 자체 검증 포함)과
`generate_answer`(N13 최종 안내)를 실제 호출한다. 검색 결과와 상위 노드의
판정을 고정한 생성 단계 비교이며 전체 검색/상담 그래프의 성능 평가는 아니다.
현재 로컬 원천 코퍼스가 없으므로 공개된 저장소 평가 정책 8개를 재사용한다.
법령 본문을 새로 수집하거나 Holdout을 열람/사용하지 않는다.

## 대상 및 조건

| 원본 | 실행 모델 | 양자화 |
| --- | --- | --- |
| Qwen/Qwen3.5-9B | qwen3.5:9b | Q4_K_M, 실행 전 API로 재확인 |
| skt/A.X-4.0-Light | hf.co/Ghiwook/A.X-4.0-Light-Q4_K_M-GGUF:Q4_K_M | 커뮤니티 변환 Q4_K_M |
| Bllossom/llama-3.2-Korean-Bllossom-3B | hf.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M:Q4_K_M | 제공자가 배포한 Q4_K_M |

사용자가 Bllossom 본 평가 시작 전에 8B를 3B로 정정했다. 질문·기대사항과
다른 두 모델의 설정은 그대로 유지한다. 최초 계획/manifest는 별도 보존한다.

- localhost Ollama만 호출. Hugging Face는 공개 모델 파일 다운로드에만 사용.
- RTX 2070 Super, GPU 우선 자동 배치. 모자라는 VRAM은 CPU/RAM offload.
- 동시에 모델 하나만 적재. 실제 GPU 적재량과 모델 digest를 기록.
- 최초 Qwen 실행에서 CUDA misaligned address와 깨진 출력이 발생했다.
  실패 결과는 `independent/`에 보존한다. Flash Attention 비활성화 후
  Qwen은 완료했으나 A.X에서 같은 오류가 반복되었다(`independent_stable/`).
  CUDA 12 모듈에서도 A.X 오류가 재현되었다. 질문/정답은 변경하지 않는다.
  최종 비교는 별도 로컬 서버 `127.0.0.1:11435`에서 세 모델 모두
  Vulkan GPU 모듈로 실행하여 `independent_final/`에 저장한다.
  `OLLAMA_LLM_LIBRARY=vulkan`, `OLLAMA_VULKAN=1`,
  `CUDA_VISIBLE_DEVICES=-1`, `GGML_VK_VISIBLE_DEVICES=0`,
  `OLLAMA_FLASH_ATTENTION=false`를 서버 프로세스에만 적용한다.
  서버 로그로 NVIDIA RTX 2070 SUPER의 실제 사용을 확인한다.
- 공통 context 8192, 출력 한도 1024, temperature 0, seed 42,
  top_p 1, top_k 0, repeat_penalty 1, presence/frequency_penalty 0, batch 128.
- 모델별 기본 채팅 템플릿은 유지한다. Qwen은 서비스의 사고 비활성화
  설정에 맞춰 think=false. 나머지는 thinking 미지원이므로 옵션을 보내지 않는다.
- 서비스의 기존 재시도/검증/템플릿 대체를 유지하고 원시 응답과 분리 기록.
- 모델마다 비채점 워밍업 후 고정 순서로 1회씩 실행. 토큰화가 다르므로
  토큰/초만으로 한국어 체감 속도를 비교하지 않는다. 캐시·배치 순서의
  영향이 있는 단일 실행 지연이며 반복 측정/통계적 유의성을 주장하지 않는다.

## 질문과 근거

1. 기존 `light_followup_dev.jsonl` 29개 전체(답변 18, 안내 11).
2. 동일 정책을 사용하는 추가 6개: 금액 오도, 온라인 신청 예외,
   미기재 서류, 법령 본문 부재, 사용자 명령 주입, 일회/연간 한도 구분.
3. 합성 N13 상태 10개: 금액, 부적격, 미확인, 범위 한정, 금액 부재,
   중복수급 불가, 다중 정책, 입력 명령 주입, 빈 결과와 지역 미확인 제어.
   합성 상태는 노드 계약 시험이며 실제 수급 판정의 정답이라고 주장하지 않는다.

실행 전 `--prepare`로 모든 케이스/기대사항을 저장하고 파일 SHA-256을
기록한다. 모델에 기대 답안이나 평가 rubric을 주입하지 않는다.

## 지표와 누출 방지

- 기존 29개 점수: 기존 스크립트와 동일한 kind + any-substring 판정.
  이것은 회귀 지표이며 사실 정확도라고 부르지 않는다.
- 형식: 엄격 JSON, 서비스 파싱 성공, 실제 생성 실패, 출력 한도 도달.
- 근거: 원문 발췌 일치, 근거 없는 숫자/조건/단정, 질문 핵심 누락.
- 보류: 답할 수 없는 질문의 직접 보류와 검증 실패에 따른 대체를 구분.
- N13: 모델 summary 채택률과 사실 템플릿 보존. 기계적 숫자 필터 통과를
  의미적 사실 정확도나 모델의 인용 능력으로 해석하지 않는다.
- 자체 consistency 검증은 서비스 동작 기록이다. 생성 모델 자신이 통과시킨
  결과를 독립적인 정답 판정으로 사용하지 않는다(Shared hallucination).
- 기존 `energy4`, `nav2`, `repair2`는 제한된 대상 범위를 설명하는 유효한
  답변도 안내로만 분류하는 라벨 모호성이 있다. legacy 점수는 보존하고,
  독립 검토에서는 근거에 충실한 범위 설명/불확실성 안내를 허용한다.
- 최종 의미 검토는 모델명을 가린 응답과 고정된 정책/합성 상태를 대조한다.
  검토자는 단일 AI 검토임을 밝히며 전문가/사용자 평가 또는 독립 표본의
  최종 검증이라고 주장하지 않는다. 사례 ID와 오류 이유를 제공한다.

## 보존 및 완료 조건

모델/런타임 메타데이터, 입력 hash, 원시 생성·검증 응답, 최종 사용자 답변,
케이스별 지표·실패를 로컬 산출물로 보존한다. 환경변수 값, 시스템 프롬프트,
비공개 원문은 결과에 저장하지 않는다. 기존 실험 결과를 덮어쓰지 않는다.
세 모델의 동일한 케이스 실행, 관련 회귀 검증, 수치와 실패 사례를 포함한
한국어 비교 보고서가 완료 조건이다.

## 출처

- [Qwen Ollama 패키지](https://ollama.com/library/qwen3.5:9b)
- [A.X 원본](https://huggingface.co/skt/A.X-4.0-Light)
- [A.X GGUF 변환 출처](https://huggingface.co/Ghiwook/A.X-4.0-Light-Q4_K_M-GGUF)
- [Bllossom 원본](https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B)
- [Bllossom GGUF](https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M)
- [Ollama chat API](https://docs.ollama.com/api/chat)

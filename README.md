<div align="center">

# 🏛️ 복지 에이전트 (Bokji Agent)

> **"추측하지 않고 오직 검증된 근거로만 답한다"**  
> 공공서비스 및 국가법령정보 기반의 **초신뢰성 멀티노드 RAG 복지 챗봇**

<br/>

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.62.0-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-000000?style=flat-square&logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![VectorDB](https://img.shields.io/badge/VectorDB-ChromaDB-blueviolet?style=flat-square)](https://www.trychroma.com/)
[![Tests](https://img.shields.io/badge/Tests-673%20Passed-success?style=flat-square&logo=pytest&logoColor=white)](tests/)

<br/>

**복지 에이전트(Bokji Agent)**는 사용자의 거주지·연령·가구/소득 상황을 대화형으로 수집하여,  
수혜 가능한 정부 지원 제도를 검색하고 **자격 요건 · 지원 금액 · 중복수급 가능 여부**를 공문서 및 법령 근거와 함께 제공하는 에이전트 서비스입니다.

</div>

---

### 👥 팀원 소개 (Team Members)

| 팀원 |                             역할                             | 담당 노드 및 핵심 파이프라인                                                                                                                                                                                                                                                                                                                                                                                          |
| :---: |:----------------------------------------------------------:|:----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **이수연**<br><br> [![GitHub](https://img.shields.io/badge/GitHub-lesoo-181717?style=flat-square&logo=github)](https://github.com/lesoo) |          **팀장, PM** <br>Graph Builder & Reasoning          | • **그래프 오케스트레이션**: LangGraph 전체 노드 배선(StateGraph) 및 파이프라인 최적화<br>• **추론·판정·검증 노드 전담 (N9 ~ N14)**<br>  - N9 자격 판정 / N10 지원금 계산 / N11 중복수급 판정<br>  - N12 결과 조립 / N13 답변 생성 / N14 최종 인용 검증<br>• **벡터 인덱싱**: Document 청킹 및 ChromaDB 벡터 적재 파이프라인 구축<br>• **평가 파이프라인**: 100문항 벤치마크 및 다중 턴 되묻기 자동 평가 러너 구축<br>• **프로젝트 총괄/통합**: 일정·품질 관리, LangGraph 노드 배선, Streamlit 연동, 코드 리뷰 및 브랜치 통합                          |
| **김일환**<br><br> [![GitHub](https://img.shields.io/badge/GitHub-KangDohwa-181717?style=flat-square&logo=github)](https://github.com/KangDohwa) | **TL(Tech Lead)** <br>Evidence Gate & Service Architecture | • **근거 검증 및 법령 검색 노드 (N7, N8)**<br>  - N7 Evidence Gate (슈퍼바이저 4-way 검증) / N8 표적 법령 정밀 검색<br>• **RAG 아키텍처 및 데이터 계약**: 계층형 지역명 표준화 및 법령 메타데이터 계약 수립<br>• **서비스 연동 & 세션 관리**: LangGraph 파이프라인과 Streamlit UI 간 E2E 어댑터 구현<br>• **코드 품질 관리**: 기술 의사결정 자문, 개발·코드 품질 기준 수립, 코드 리뷰 및 브랜치 통합                                                                                                                      |
| **허유나**<br><br> [![GitHub](https://img.shields.io/badge/GitHub-Heoyuna0819-181717?style=flat-square&logo=github)](https://github.com/Heoyuna0819) |               **Law Data & Retrieval Nodes**               | • **정책 검색 및 원문 대조 노드 (N4 ~ N6)**<br>  - N4 정책 검색 Agent / N5 Claim Plan 분해 / N6 공식 공고·지침 대조<br>• **법령 데이터 파이프라인**: 국가법령정보센터 19만 건(법령·규칙·조례) 대규모 수집 및 정제<br>• **모델 평가 실험**: 구조화 출력(Structured Output) 및 LLM-as-judge 원문 대조 실험                                                                                                                                                                               |
| **김길환**<br><br> [![GitHub](https://img.shields.io/badge/GitHub-amygdalis24-181717?style=flat-square&logo=github)](https://github.com/amygdalis24) |                **Subsidy Data Engineering**                | • **공공서비스 데이터 파이프라인**: 공공데이터포털 공공서비스 API 3종(목록·상세·조건) 전수 수집<br>• **데이터 정규화**: 10,968건 복지 정책 데이터 병합, 결측치 정제 및 Document 스키마 변환<br>• **데이터 품질 관리**: 수집 데이터 인수 검증(Handoff Validation) 및 매니페스트 명세화                                                                                                                                                                                                             |
| **주상현**<br><br> [![GitHub](https://img.shields.io/badge/GitHub-shju0924--ai-181717?style=flat-square&logo=github)](https://github.com/shju0924-ai) |            **Slot Parsing, Frontend UI & Auth**            | • **슬롯 파싱 및 대화 제어 노드 (N1 ~ N3)**<br>  - N1 슬롯 파싱 / N2 적합성 체크(지역 하드 게이트) / N2a 일반 법령 참고 / N3 재질문(되묻기)<br>• **프론트엔드 UI**: Streamlit 기반 대화형 웹 챗봇, 요약 진단 카드 및 정책 캐러셀 구현<br>• **사용자 인증 & 보안**: SQLite 회원 관리 및 Fernet 대칭키 기반 개인정보(PII) 암호화 저장소 구축<br>• **RAG 기초 설계**: 1차 Document/Chunk 스키마 정의 및 초기 ChromaDB 벡터스토어 구축                                                                                           |

---

## 📌 목차 (Table of Contents)

- [✨ 핵심 가치 및 차별점](#-핵심-가치-및-차별점-core-values)
- [🏗️ 시스템 아키텍처](#system-architecture)
- [🧩 에이전트 파이프라인 (LangGraph N1-N14 Pipeline)](#-에이전트-파이프라인-langgraph-n1-n14-pipeline)
- [🛠 기술 스택](#-기술-스택-tech-stack)
- [📊 데이터셋 및 벡터 색인 사양](#-데이터셋-및-벡터-색인-사양-dataset--index)
- [📂 프로젝트 구조](#-프로젝트-구조-project-structure)
- [🚀 시작하기 (Quick Start)](#-시작하기-quick-start)
  - [사전 요구사항](#사전-요구사항)
  - [설치 및 설정](#설치-및-설정)
  - [환경 변수 (.env)](#환경-변수-설정-env)
  - [실행](#서비스-실행)
- [🧪 테스트 및 벤치마크](#-테스트-및-벤치마크-testing--evaluation)
- [⚠️ 투명한 한계 및 엔지니어링 고려사항](#️-투명한-한계-및-엔지니어링-고려사항-known-limitations)
- [📄 프로젝트 문서 및 컴플라이언스](#-프로젝트-문서-및-컴플라이언스-documentation)

---

## ✨ 핵심 가치 및 차별점 (Core Values)

```
[ 단 하나의 대원칙 ]
"검색된 공적 근거로 명확히 입증된 내용만 답한다."
근거가 부족하면 어설프게 추측하지 않고 "미확인"으로 분류하며 공식 접수처 안내로 보류합니다.
```

1. **환각 억제를 위한 다단계 검증**
   - 정책 문서에서 주장(Claim)을 추출하고 원문과의 교차 대조(N6) 및 Evidence Gate(N7)를 통과해야만 답변 생성에 반영됩니다.
2. **동적 슬롯 파싱 & 하드 게이팅 (Interrupt & Resume)**
   - 지역 등 필수 조건이 누락된 경우 즉각 질문을 중단하고 필요한 항목만 자연스럽게 되물어 정확한 대상 정책을 필터링합니다.
3. **자격 · 지원금 · 중복수급 삼각 판정**
   - 단순히 정책을 요약하는 것을 넘어, 사용자의 슬롯 조건을 기반으로 **충족/미충족/미확인**, **계산 가능한 지원금액**, **타 복지와의 중복수급 제한**을 구조화하여 산출합니다.
4. **규칙 기반 안전 폴백(Graceful Fallback)**
   - 외부 LLM API 지연/단절 시에도 서비스 전체가 멈추지 않고, 규칙 기반 템플릿과 검증된 DB 데이터로 안전하게 즉시 대체 응답합니다.

---

<a name="system-architecture"></a>
## 🏗️ 시스템 아키텍처

온라인 상담 런타임과 오프라인 데이터 수집·색인 경로를 분리한 전체 시스템 구성입니다.

![Bokji Agent 시스템 아키텍처](docs/images/bokji-agent-system-architecture.png)

> 세부적인 N1~N14 제어 흐름은 아래의
> [에이전트 파이프라인](#-에이전트-파이프라인-langgraph-n1-n14-pipeline)을 참고하세요.

---

## 🧩 에이전트 파이프라인 (LangGraph N1-N14 Pipeline)

복지 에이전트는 사용자의 질문을 단순 생성하지 않고, **14개의 LangGraph 상태 노드**를 통해 팩트체크 및 자격 검증을 거친 후 안전하게 답변합니다.

> 💡 GitHub 웹에서 펼치면 인터랙티브 다이어그램으로 렌더링되어 확대 및 노드 탐색이 가능합니다.

```mermaid
flowchart TD
    user(["🚀 USER INPUT"]):::c_input

    subgraph S1 ["1. 슬롯 파싱 및 검증"]
        direction LR
        n1["N1. 슬롯 파싱<br/><small>지역 · 생년월일 · 성별 · 소득 · 장애 · 취업 · 관심사 등</small>"]:::c_node
        n2["N2. 슬롯 완전성 체크 (Gate)<br/><small>지역 + 프로필 5종 하드 게이트 · 슬롯별 최대 2회 질문</small>"]:::c_gate
        n2a["N2a. 일반 법령 참고 검색<br/><small>지역 부족 시 law_type=law · 전국 공통</small>"]:::c_node
        n3["N3. 추가 정보 요청<br/><small>부족 슬롯 일괄 질문 · Interrupt & Resume</small>"]:::c_interrupt

        n1 -->|"E2: slots"| n2
        n2 -->|"E4: missing_region"| n2a
        n2 -->|"E4: profile missing"| n3
        n2a -->|"E5: general_law_references"| n3
        n3 -.->|"E6: resumed user_input"| n1
    end

    subgraph S2 ["2. 정책 검색 및 근거 검증"]
        direction TB

        subgraph S2_top [" "]
            direction LR
            n4["🔍 N4. 정책 검색 Node<br/><small>프로필 필터 후 Top-N 후보 검색</small>"]:::c_search
            n5["📋 N5. Claim Plan 수립<br/><small>자격 · 금액 · 중복수급 Claim · 현재 doc_check_required=true</small>"]:::c_node
            n6["📑 N6. 색인 정책문서 근거 대조<br/><small>subsidy chunk에서 발췌 문장의 문자열 일치 확인</small>"]:::c_node

            n4 -->|"E7: subsidy_chunks + legal_basis"| n5
            n5 -->|"E8: claim_plan"| n6
        end

        n7["🛡️ N7. Evidence Gate<br/><small>근거 ID · 시행일 · 충돌 · safety_blocked 신호 확인</small>"]:::c_gate
        n8["⚖️ N8. 표적 법령 검색 Node<br/><small>required_law_sources의 law_type · source_id 정확 조회</small>"]:::c_search
        n7stop["abstain_insufficient_evidence<br/><small>N7 fail 고정 응답 · answer_status=abstained</small>"]:::c_fail

        n6 -->|"E10: 갱신된 claim_plan"| n7
        n7 -.->|"E11: 문서 근거 부족 · N6 재호출"| n6
        n7 -->|"E12: 법령 근거 부족"| n8
        n8 -.->|"E13: law_chunks + claim_plan"| n7
        n7 -->|"E14: fail"| n7stop
    end

    subgraph S3 ["3. 병렬 판정 및 결과 조립"]
        direction TB

        n9["🎯 N9. 자격 판정<br/><small>충족 · 미충족 · 미확인 · 현재 직접 대조 가능한 조건은 연령</small>"]:::c_node

        subgraph S3_mid [" "]
            direction LR
            n10["🧮 N10. 지원금 처리<br/><small>지원내용 재검색 · 단일 금액 추출 · 근거가 있을 때 제한적 총액 계산</small>"]:::c_node
            n11["🔗 N11. 중복수급 판정<br/><small>불가 · 조건부 · 미확인 · 가능 자동 판정 없음</small>"]:::c_node
        end

        n12["📦 N12. 결과 조립<br/><small>정책별 결합 · 금액 계산 불가 시 관련 법령 링크 조회</small>"]:::c_node

        n9 -->|"E16: eligibility_verdicts"| n10
        n9 -->|"E17: eligibility_verdicts"| n11
        n10 -->|"E18: benefit_amounts"| n12
        n11 -->|"E19: duplicate_verdicts"| n12
    end

    subgraph S4 ["4. 답변 생성 및 최종 검증"]
        direction TB

        n13["💬 N13. 답변 생성<br/><small>assembled_result 초안화 · evidence_chunk_id를 source_url로 변환</small>"]:::c_node
        n14["🔍 N14. 최종 인용 검증 (Gate)<br/><small>citation chunk_id 존재 확인 · 응답 상태 결정</small>"]:::c_gate

        subgraph S4_bot [" "]
            direction LR
            success["✅ 응답 반환<br/><small>answer_status=complete 또는 partial</small>"]:::c_success
            abstained["❗ 확인 불가 응답<br/><small>answer_status=abstained</small>"]:::c_fail
        end

        n13 -->|"E21: draft_answer + citations"| n14
        n14 -->|"E23: complete 또는 partial"| success
        n14 -->|"E22: abstained"| abstained
    end

    %% 단계 간 연결
    user -->|"E1"| n1
    n2 -->|"E3: sufficient"| n4
    n7 -->|"E15: pass"| n9
    n7stop -->|"고정 안내문"| abstained
    n12 -->|"E20: assembled_result"| n13

    %% 노드 스타일
    classDef c_input fill:#F3E8FD,stroke:#7C3AED,stroke-width:2px,color:#1E1B4B
    classDef c_node fill:#FFFFFF,stroke:#3B82F6,stroke-width:1.5px,color:#1E293B
    classDef c_gate fill:#FEF3C7,stroke:#D97706,stroke-width:2px,color:#78350F
    classDef c_search fill:#ECFDF5,stroke:#10B981,stroke-width:2px,color:#064E3B
    classDef c_interrupt fill:#FAF5FF,stroke:#A855F7,stroke-width:1.5px,stroke-dasharray:4 4,color:#581C87
    classDef c_success fill:#ECFDF5,stroke:#059669,stroke-width:2px,color:#064E3B
    classDef c_fail fill:#FEF2F2,stroke:#EF4444,stroke-width:2px,color:#991B1B

    %% 컨테이너 스타일
    style S2_top fill:none,stroke:none
    style S3_mid fill:none,stroke:none
    style S4_bot fill:none,stroke:none
```

<details>
<summary><b>🔍 각 노드별 세부 역할 및 구현 파일 보기</b></summary>

| 노드 | 구현 모듈 | 핵심 역할                                                |
|:---:|:---|:-----------------------------------------------------|
| **N1** | `graph/nodes/slot_parser.py` | LLM을 이용하여 사용자 입력 문장에서 슬롯(나이, 지역, 상황) 추출 (Rule + LLM) |
| **N2** | `slot_completeness_gate.py` | 서비스 진행을 위한 필수 조건(거주 지역 등) 충족 여부 판정                   |
| **N2a**| `general_law_reference_search.py` | 지역 정보 미확인 상태에서 안내할 수 있는 전국 단위 참고 법령 탐색               |
| **N3** | `request_missing_slots.py` | 사용자에게 빠진 슬롯을 되묻고 그래프 실행 일시 정지 (`interrupt`)          |
| **N4** | `policy_search.py` | 사용자 슬롯 조건에 부합하는 후보 지원제도 Top-K 벡터 검색                  |
| **N5** | `claim_plan.py` | LLM을 이용하여 후보 정책 원문에서 자격·지원금·중복수급 주장(Claim) 추출                  |
| **N6** | `document_verification.py` | 추출된 주장이 실제 수집 원문에 존재하는지 팩트체크 대조                      |
| **N7** | `evidence_gate.py` | 근거의 충분성·시행일자 유효성·법령 충돌 여부를 통제하는 슈퍼바이저 게이트            |
| **N8** | `targeted_law_search.py` | 선언된 법령명 기반 정밀 타겟 메타데이터 검색                            |
| **N9** | `eligibility_verdict.py` | LLM을 이용하여 조건 대조를 통한 현재 구현 범위 내 자격 판정 (`충족` / `미충족` / `미확인`)    |
| **N10**| `benefit_calculator.py` | 공식 근거가 확보된 명확한 산식에 한해서만 지원금액 계산                      |
| **N11**| `duplicate_benefit.py` | 타 정부지원 사업과의 중복수급 허용 여부 판정                            |
| **N12**| `result_assembly.py` | 정책별 판정 및 근거 데이터 정합성 조립 (근거 없는 합산 방지)                 |
| **N13**| `answer_generation.py` | 신뢰할 수 있는 최종 사용자 맞춤형 설명문 LLM을 이용하여 생성                           |
| **N14**| `final_verification.py` | 인용 `chunk_id` 존재 여부 확인 및 최종 응답 상태 결정                 |

</details>

---

## 🛠 기술 스택 (Tech Stack)

| 계층 | 기술 / 도구 | 선정 및 사용 이유 |
| :--- | :--- | :--- |
| **Orchestration** | `LangGraph`, `LangChain` | 14개 노드 간 조건부 라우팅 및 Stateful Checkpointing (`MemorySaver`) |
| **LLM & Inference** | `Qwen/Qwen3.5-9B`, HuggingFace API, RunPod | 한국어 문맥 이해 및 추론 지원, API 단절 시 규칙 기반 Fallback 탑재 |
| **Embedding & Vector DB** | `ChromaDB`, `intfloat/multilingual-e5-base` | 768차원 다국어 고밀도 벡터 임베딩 및 메타데이터 필터링 |
| **Frontend UI** | `Streamlit 1.62.0` | 대화형 챗, 정책 비교 캐러셀, 진단 카드 및 실시간 노드 타이밍 시각화 |
| **Data & Scraping** | `Python 3.11`, `Requests`, `xmltodict` | 공공데이터포털(공공서비스) 및 국가법령정보센터 대규모 수집 |
| **Security & Storage** | `SQLite`, `bcrypt`, `cryptography (Fernet)` | 사용자 프로필 PII(개인식별정보) 안전 암호화 저장 |
| **Quality & Testing** | `pytest`, `pytest-subtests`, `Streamlit AppTest` | 600개 이상의 엄격한 회귀 테스트 및 가상 위젯 트리 렌더링 검증 |

---

## 📊 데이터셋 및 벡터 색인 사양 (Dataset & Index)

신뢰할 수 있는 공공 출처의 데이터를 가공하여 다중 인덱스를 운용합니다.

### 1. 원천 데이터 (`data/processed/`)
- **공공서비스 지원제도 문서 (`subsidy_documents.jsonl`)**: `10,968건` (43.3 MB)
- **국가법령정보센터 법령 메타데이터 (`law_documents.jsonl`)**: `190,445건` (396.0 MB)

### 2. 벡터 색인 사양 (`data/vector_db/`, 총 2.9 GB)
| 제공자 (Provider) | 차원 | Subsidy 청크 | Law 청크 | 특징 및 용도 |
| :--- | :---: | :---: | :---: | :--- |
| **`korean`** *(운영 표준)* | **768** | **60,497** | **111,500** | `multilingual-e5-base` 기반 실 서비스 의미론적 검색 |
| **`local-hash-v1`** | 128 | 45,413 | 1,465 | 네트워크/GPU가 없는 오프라인 스모크 테스트 전용 |

> ⚠️ **법령 데이터 범위 준수:** 국가법령정보센터 목록조회 메타데이터(`metadata_only`)를 의도적으로 타겟팅하여, 법적 자격을 무단 확정하지 않고 관련 법률 목록 및 공식 웹사이트 이동 링크만을 안전하게 안내합니다.

---

## 📂 프로젝트 구조 (Project Structure)

```plaintext
bokji-agent/
├── app.py                         # Streamlit 애플리케이션 진입점
├── streamlit_ui/                  # 화면 프레젠테이션 계층
│   ├── pages/                     # chat / auth / mypage 뷰
│   ├── pipeline.py                # 공식 서비스 계층 API 어댑터
│   ├── rendering.py               # 위젯(요약 카드, 정책 캐러셀, 근거 뷰어) 렌더러
│   └── session.py                 # 세션 및 사용자 상태 관리
├── src/rag_chatbot/               # 핵심 백엔드 패키지
│   ├── service.py                 # ask() / answer_followup() 메인 인터페이스
│   ├── graph/                     # LangGraph 파이프라인 엔진
│   │   ├── builder.py             # N1~N14 노드 배선 및 그래프 빌더
│   │   ├── nodes/                 # 개별 기능 노드 모듈 (N1 ~ N14)
│   │   ├── slot_schema.py         # 슬롯 정의 및 게이트 규칙
│   │   └── llm_gateway.py         # LLM 호출 제어 및 보정 게이트웨이
│   ├── auth/                      # 회원 관리 및 PII 암호화 (Fernet/bcrypt)
│   ├── collectors/                # 공공서비스 / 법령정보센터 수집 스크립트
│   └── timing.py                  # 노드별 레이턴시 계측 프로파일러
├── rag_design/                    # 데이터 청크/벡터스토어 스키마 공용 계약
├── scripts/                       # 진단, 재색인 및 벤치마크 유틸리티
├── tests/                         # 단위/통합/UI 검증 테스트 슈트 (673+ Passed)
└── docs/                          # 시스템 설계서 및 준수 기준(Gate 0~6)
```

---

## 🚀 시작하기 (Quick Start)

### 사전 요구사항
- **Python**: `3.11.x` 권장
- **Streamlit**: 반드시 `1.62.0` 사용 (버전 불일치 시 UI 렌더링 에러 발생)

### 설치 및 설정

```bash
# 1. 저장소 복제
git clone https://github.com/SKN33-3rd-3Team/bokji-agent.git
cd bokji-agent

# 2. 의존성 패키지 설치
pip install -r requirements.txt
pip install -r requirements-streamlit.txt

# 3. 환경 변수 템플릿 복사
cp .env.example .env
```

### 환경 변수 설정 (`.env`)

주요 설정 항목은 다음과 같습니다. (`.env` 파일은 보안상 절대 커밋하지 마세요.)

| 변수명 | 필수 여부 | 기본값 / 예시 | 설명 |
| :--- | :---: | :--- | :--- |
| `EMBEDDING_PROVIDER` | **필수** | `korean` | 임베딩 엔진 (`korean`: e5-base, `hash`: 오프라인용) |
| `EMBEDDING_MODEL_NAME`| 선택 | `intfloat/multilingual-e5-base` | 사용할 HuggingFace 임베딩 모델명 |
| `HF_TOKEN` | 선택 | `hf_...` | HuggingFace Inference API 액세스 토큰 |
| `LLM_MODEL_NAME` | 선택 | `Qwen/Qwen3.5-9B` | 추론 모델명 |
| `LLM_MAX_NEW_TOKENS` | 권장 | `4096` | 생성 토큰 상한 (낮을 시 추론 중 끊김 방지용) |
| `BOKJI_TRACE` | 선택 | `1` | `1`로 설정 시 콘솔에 노드별 추론 로그 출력 |
| `AUTH_ENC_KEY` | 선택 | `(Fernet key)` | 개인정보(PII) DB 암호화 대칭키 |

> ⚠️ **데이터 준비:** 전체 공공서비스 문서와 법령 메타데이터, ChromaDB 벡터 인덱스는 Git 저장소에 포함되지 않습니다. 서비스 실행 전에 원천 문서를 준비한 뒤 `python scripts/reindex_korean.py`로 인덱스를 생성해 주세요.
> 💡 **Tip (Zero-LLM 모드):** LLM API 키를 설정하지 않더라도 모든 노드가 자체 **규칙 기반(Rule-based) 엔진**으로 자동 폴백되어 정상 작동합니다.

### 서비스 실행

```bash
# Streamlit 웹 대시보드 실행
streamlit run app.py
```

> **Windows PowerShell 환경에서 모듈 경로 에러 발생 시:**
> ```powershell
> $env:PYTHONPATH = ".;src"
> streamlit run app.py
> ```

<details>
<summary><b>🛠️ 유용한 CLI 진단 도구들</b></summary>

```bash
# 1. 터미널 대화형 챗봇 실행
python scripts/interactive_console_chat.py

# 2. 파이프라인 노드별 레이턴시 및 전체 흐름 수동 진단
python scripts/manual_test_service.py

# 3. LLM API 연결 상태 및 할당량 진단
python scripts/check_llm_connection.py

# 4. 한국어 벡터 인덱스 재생성 (GPU 가속 지원)
python scripts/reindex_korean.py --device cuda
```

</details>

---

## 🧪 테스트 및 벤치마크 (Testing & Evaluation)

안정적인 복지 서비스 제공을 위해 단위 기능부터 E2E UI 위젯 렌더링까지 다층 회귀 테스트 파이프라인을 운영합니다.

```bash
# 전체 테스트 실행 (현재 673개 테스트 통과)
python -m pytest -q

# 핵심 영역별 개별 테스트
python -m pytest -q tests/test_graph_nodes.py          # 14개 노드 개별 로직
python -m pytest -q tests/test_service.py              # ask() 서비스 계약
python -m pytest -q tests/test_streamlit_rendering.py  # Streamlit 위젯 렌더링

# 표준 100문항 벤치마크 자동 검증 실행
python scripts/run_dev_validation.py
```

---

## ⚠️ 투명한 한계 및 엔지니어링 고려사항 (Known Limitations)

우리는 프로젝트의 기술적 한계를 투명하게 공유합니다 ([`docs/PROJECT_COMPLIANCE.md`](docs/PROJECT_COMPLIANCE.md)).

- **응답 대기 시간(Latency)**: 다단계 환각 검증(N5~N7) 과정에서 후보 정책마다 세부 주장을 추출하므로 수십 초가 소요될 수 있습니다. (진행률 표시줄 및 캐싱으로 완화 중)
- **세션 영속성**: `MemorySaver`를 기반으로 대화 문맥을 메모리에 상주시키므로, 서버 프로세스 재시작 시 진행 중이던 되묻기 세션이 초기화됩니다.

---

## 📄 프로젝트 문서 및 컴플라이언스 (Documentation)

| 문서명 | 내용 요약 |
| :--- | :--- |
| 📋 [`docs/PROJECT_COMPLIANCE.md`](docs/PROJECT_COMPLIANCE.md) | 프로젝트 준수 기준, Gate 0~6 단계별 통과 규정 및 법령 데이터 범위 |
| 📐 [`docs/RAG_DESIGN_PLAN.md`](docs/RAG_DESIGN_PLAN.md) | RAG 청킹, 검색, 노드 인터페이스 통합 아키텍처 설계서 |
| 🗄️ [`docs/VECTOR_STORE.md`](docs/VECTOR_STORE.md) | ChromaDB 스키마 계약 및 메타데이터 정합성 규격 |
| 🧪 [`docs/EVALUATION_AUTOMATION.md`](docs/EVALUATION_AUTOMATION.md) | 100건의 평가 데이터셋 기반 자동 정량 평가 가이드 |
| 🔒 [`docs/PII_LOGGING.md`](docs/PII_LOGGING.md) | 사용자 민감정보(소득, 주민등록상황 등) 처리 및 로깅 정책 |
| 🤝 [`CONTRIBUTING.md`](CONTRIBUTING.md) | 브랜치 네이밍 컨벤션 (`type/#이슈-설명`) 및 코드 리뷰 규칙 |



## 한줄 회고

| 이름 | 회고                                                                                                                                                                      |
|---|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **수연** | 팀장이자 PM을 처음 맡게 되었는데, 프로젝트 난이도를 낮게 예상한 탓에 일정이 밀린것이 가장 아쉬웠지만 동시에 기술적으로도 많은 걸 시도해보고 배울 수 있었다. 진행이 순탄치는 않았지만 목표로 정한 수치는 달성해서 다행이라고 생각하고, 끝까지 함께 해준 팀원분들께 감사하다.              |
| **일환** | 흥미가 있던 주제였기에 생각보다 수월하고 간단할 것 같았던 프로젝트였으나, 진행 과정에서 전혀 만만치 않았음을 느꼈다. 그럼에도 팀장님의 리드와 팀원분들의 도움으로 무사히 프로젝트를 마칠 수 있었다고 생각하여 감사하다.                                              |
| **유나** | 처음엔 그냥 챗봇 하나 만드는 줄 알았는데, 실제로 해보니 검색-검증-계산을 다 따로 쪼개고 검증해야 겨우 믿을 만한 답이 나온다는 걸 알게 된 프로젝트였다. 데이터 하나, 모델 하나를 정하는 데도 실험으로 근거를 만들어야 한다는 걸 몸으로 익히게 되는 경험이었다.                    |
| **길환** | 개인사정으로 인해 프로젝트 개발부분에서 참여도가 낮았던 부분이 가장 아쉽습니다. 그래도 발표자였던 덕분에 이번 프로젝트와 관련된 지식과 설계과정에 대해서 공부할 수 있었으며, 4차 프로젝트 때는 현 프로젝트에 대한 이해도를 높여 개발과정에도 더 기여할 수 있었으면 좋겠습니다.              |
| **상현** | 이번 프로젝트는 간단할거라 생각했는데 생각보다 설계해야할것도 많았고 구현하고, 고려해야할 요소들도 많았었다.<br>그래도 팀원들이랑 으쌰으쌰 하고 많은 도움을 받아서 이번 프로젝트를 잘 마무리 할 수 있었던것 같다.<br>보완 할수 있는 부분 보완하고 4차때는 조금 더 발전 할 수 있도록 해야겠다. |
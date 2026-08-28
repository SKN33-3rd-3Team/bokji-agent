# 협업 규칙

프로젝트 기준은 `docs/PROJECT_COMPLIANCE.md`를 따른다.

## 작업 흐름

1. Issue에 목적, 범위, 관련 Gate, 완료 조건과 검증 방법을 적는다.
2. 최신 `main`에서 만든 작업 브랜치에서 한 목적만 작은 커밋으로 작업·검증하고, 실험은 한 조건만 바꾼다.
3. PR에 변경 이유, 변경 내용, 검증 결과와 남은 한계를 적는다.

## 통상적인 형식

- Issue 제목: `[type] 한국어 요약`
- Issue 본문: `목적 / 범위 / 관련 Gate / 완료 조건·검증`
- 브랜치: `<type>/<issue-number>-<short-slug>`
  - 하나의 Issue가 독립적으로 구현 가능한 여러 단위(예: 그래프 노드)로 나뉘면, 같은 issue-number에 slug만 다르게 붙여 여러 브랜치로 나눠 작업할 수 있다. 이때 slug에 단위 식별자를 포함한다 (예: `feat/11-n9-eligibility-verdict-node`, `feat/11-n10-benefit-calculator-node`). PR은 마지막 하나만 이슈를 닫고(`closes #11`), 나머지는 `part of #11`로 남겨 조기 종료를 막는다.
- Commit 및 PR 제목: `<type>(<scope>): <한국어 요약>` (`scope`가 불명확하면 생략)
- `type`: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `build`, `ci`, `perf`, `style` 중 선택
- Commit은 한 논리 변경 단위로 작성하고 PR 본문은 기존 최소 템플릿을 사용한다.

다른 사람의 변경을 임의로 되돌리거나 Force Push하지 않는다. Merge 방식은
PR 단위 기록이 명확한 Squash를 권장한다.

## 그래프 노드 구성 (src/rag_chatbot/graph/)

LangGraph 기반 답변 파이프라인은 노드 단위로 나누어 병렬 작업한다.

- 노드 1개 = 파일 1개. `src/rag_chatbot/graph/nodes/` 아래에 노드마다 별도 파일을 만든다.
- 파일명은 `snake_case`로 노드의 역할을 나타내고, 노드 번호(N9 등)는 파일명에 넣지 않는다.
  번호는 브랜치·PR·문서에서만 쓴다 - 설계가 바뀌면 번호가 바뀔 수 있어서다.
  예: `eligibility_verdict.py`, `benefit_calculator.py`.
- 함수 시그니처는 `def <동사_명사>(state: GraphState) -> dict:` 형태를 따르고,
  반환값은 `GraphState`의 일부 필드만 갱신하는 partial dict로 작성한다 (LangGraph 상태 병합 방식).
- 판단(조건 분기, 재시도 루프 등 그래프 흐름을 바꾸는 권한)을 가진 노드만 "Agent"라고
  부른다. 그 외는 "Node"이며, 파일/함수 이름에 임의로 Agent를 붙이지 않는다.
- 공용 상태 스키마 `state.py`는 담당자 1인이 변경 제안 -> 리뷰 -> 반영 순서로만 수정한다.
  다른 사람이 이미 쓰고 있는 필드를 임의로 바꾸거나 지우지 않는다.
- 새 노드를 추가하면 `nodes/__init__.py`에 함수를 re-export하고, PR 설명에 어떤 state
  필드를 읽고/쓰는지 명시한다.
- 구현 전 시그니처와 docstring만 있는 stub 상태(`NotImplementedError`)로 먼저 병합할 수
  있다. 이 경우 PR 설명에 stub임을 표시한다.

```
src/rag_chatbot/graph/
├── __init__.py
├── state.py                    # GraphState 및 하위 TypedDict (공용, 변경 시 리뷰 필수)
└── nodes/
    ├── __init__.py              # 노드 함수 re-export
    └── {node_name}.py           # 노드 함수 파일
```


## 실험

- Baseline 이후에만 개선을 실험한다.
- 한 조건만 바꾸어 같은 Dev set으로 비교하고 Baseline·실패 결과를 보존한다.
- Holdout은 최종 설정 확정 뒤 Gate 6 최종 검증에서만 사용한다. 데이터·청킹·평가 조건이 바뀌면 관련 설명도 갱신한다.

## Merge 조건

- 관련 검증이 통과했다.
- PR은 작성자가 아닌 팀원 1명이 승인한 뒤 병합한다.

## GitHub 목표 설정

- `main` 변경은 PR을 통하도록 설정한다.
- `main`의 삭제와 Force Push를 차단한다.
- 실제로 성공한 CI check만 이후에 필수 상태 검사로 지정한다.

원격 설정 전에는 현재 상태와 변경안을 읽기 전용으로 확인해 보고한다.
파일 변경 승인과 별개로, 원격 설정에 대한 명시적 승인을 받은 뒤 적용한다.

## 보안

- API Key, 환경변수 값, 개인정보와 내부 원문을 저장소에 올리지 않는다.
- 공개가 허용된 샘플 문서만 추적한다.
- 실패한 검증과 알려진 한계를 숨기지 않는다.

# bokji-agent frontend (React + Vite)

`docs/PROJECT_STRUCTURE.md`, `docs/API_정의서.xlsx`, `docs/요구사항_정의서.xlsx`,
`docs/복지에이전트_디자인시안.html` 기준으로 구현한 React 프론트엔드입니다.
`backend/`(FastAPI)는 이번 작업 범위가 아니며 아직 없습니다 — API 호출은
`API_정의서.xlsx` 계약대로 실제 엔드포인트를 부르도록 작성되어 있어, 백엔드가
없는 상태에서 실행하면 네트워크 에러/로딩 상태로만 나타납니다.

## 실행

```bash
npm install
cp .env.example .env   # VITE_API_BASE_URL 확인/수정
npm run dev
```

## 알려진 보류 항목

- **구비서류(S07-06/S10-01)**: `PolicyDetail.required_documents`(및 `_official`/`_self`)가
  배열이 아니라 원문 문자열 1개라, 디자인시안처럼 칩 여러 개로 쪼개지 않고
  원문 한 줄 그대로 표시한다. 배열 필드가 추가되면 `PolicyDetailView.tsx`/
  `PolicyCompareTable.tsx`의 해당 부분만 칩 렌더링으로 교체하면 된다.

# bokji-agent frontend (React + Vite)

`docs/PROJECT_STRUCTURE.md`, `docs/API_정의서.xlsx`, `docs/요구사항_정의서.xlsx`,
`docs/복지에이전트_디자인시안.html` 기준으로 구현한 React 프론트엔드입니다.
`backend/`의 FastAPI 엔드포인트와 연동합니다. 백엔드 설치·실행 방법은
[백엔드 안내](../backend/README.md)를 참고하세요.

## 기술 스택

- **React 18 + TypeScript + Vite**
- **react-router-dom v6** — 라우팅
- **@tanstack/react-query v5** — 서버 상태 캐싱(`staleTime`/`gcTime` 전략은
  각 훅 주석 참고 — 특히 자동추천/프로필 캐시는 무효화 시점이 중요하다)
- **axios** — API 클라이언트(`src/api/client.ts`, `withCredentials: true`로
  세션 쿠키 포함)

## 화면 구성

| 화면 | 파일 | 설명 |
| --- | --- | --- |
| 로그인 | `pages/LoginPage.tsx` | 로그인 성공 시 홈(`/home`)으로 이동 |
| 회원가입 | `pages/SignupPage.tsx` | 계정 정보(필수) + 선택 프로필 정보 입력 |
| 홈 (자동 추천) | `pages/HomePage.tsx` | 로그인 직후 1회, 저장된 프로필만으로 자동 정책 추천(API-14) |
| 채팅 | `pages/ChatPage.tsx` | 자유 상담, 되묻기·슬롯 충돌·계산 폼·정책 카드/상세/비교 전부 포함 |
| 마이페이지 | `pages/MyPage.tsx` | 프로필 조회/수정, 비밀번호 변경, 회원 탈퇴 |

화면별 요구사항 상세는 `docs/요구사항_정의서.xlsx`(S-01~S-11 시트)를 참고하세요.

## 프로젝트 구조

```
src/
├── api/            # axios 호출 함수 (authApi/chatApi/configApi/userApi + client.ts)
├── components/
│   ├── chat/       # 채팅 메시지, 되묻기 폼, 정책 카드/상세/비교, 진행률 막대 등
│   ├── common/       # PillMultiSelect, Toast, ConfirmModal 등 재사용 UI
│   ├── dialogs/    # 정책 문의 채팅 다이얼로그
│   └── layout/     # AppShell(헤더/로고), Sidebar
├── constants/      # 라벨·폴백 옵션 상수 (labels.ts)
├── features/
│   ├── auth/       # useAuth, useMyPage(프로필 조회/수정 훅)
│   ├── chat/       # useChatSession(상담 세션), useAutoRecommendations(홈), useChatProgress
│   └── config/     # useSearchOptions(API-09 옵션 캐시)
├── pages/          # 위 화면 구성 표 참고
├── types/          # API 응답 타입 (chat.ts/auth.ts)
└── utils/          # policy.ts(카드 요약/구비서류 파싱 등), chatQuestion.ts
```

## 실행

### 1) 실제 백엔드에 연결

```bash
npm install
cp .env.example .env   # VITE_API_BASE_URL 확인/수정
npm run dev
```

`backend/`를 별도로 띄워야 합니다(설치·실행은 [백엔드 안내](../backend/README.md) 참고).
`AUTH_DB_URL`(회원 DB), `RUNPOD_POD_ID`/`HF_TOKEN`(LLM) 등 백엔드 환경변수가
없으면 회원가입·채팅이 동작하지 않습니다.

### 2) 목(mock) 서버로 프론트만 확인

실제 백엔드 없이 화면 동작만 빠르게 확인하고 싶을 때 씁니다.

```bash
npm install
npm run mock-server   # frontend/mock-server.js, http://localhost:8000
```

다른 터미널에서 `npm run dev`로 프론트를 띄우면 `VITE_API_BASE_URL` 기본값
(`http://localhost:8000`)이 이 목 서버를 그대로 가리킵니다. 목 서버는
**메모리 기반이라 재시작하면 가입한 계정이 전부 초기화**됩니다.

## 환경변수

`.env.example` 참고:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:8000` | 백엔드(또는 mock-server) 주소 |

## 빌드

```bash
npm run build     # tsc -b && vite build
npm run preview   # 빌드 결과 로컬 미리보기
```

## 참고

- 구비서류(`PolicyDetail.required_documents` 등)는 배열이 아니라 원문
  문자열 1개다 — `utils/policy.ts`의 `documentChipItems()`가 줄바꿈이
  2줄 이상이면 칩으로, 아니면 원문 문단 그대로 표시하도록 처리한다
  (원본 없는 구분자를 새로 만들지 않는다는 원칙, `streamlit_ui/rendering.py`
  로직 포팅).
- 타입 체크만 빠르게 돌리려면 `npx tsc -b --noEmit`.

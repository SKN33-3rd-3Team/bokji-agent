# bokji-agent backend (FastAPI)

`src/rag_chatbot/`(기존 코어)를 그대로 재사용하는 얇은 REST API 계층.
API 계약은 `API_정의서.xlsx`, 화면별 요구사항은 `요구사항_정의서.xlsx`,
전체 폴더 배치는 `PROJECT_STRUCTURE.md`를 따른다(세 파일 모두 레포에
커밋되지 않고 팀 공유 참고자료 폴더에서 관리된다 - `docs/`에는 없다).

## 설치

레포 루트에서:

```bash
pip install -r backend/requirements-backend.txt
```

## 실행

레포 루트에서 (import 경로 때문에 반드시 레포 루트에서 실행):

```bash
uvicorn backend.app.main:app --reload --port 8000
```

`.env`는 레포 루트의 기존 `.env`를 그대로 읽는다(`HF_TOKEN`/`AUTH_ENC_KEY`/
`RUNPOD_POD_ID` 등). FastAPI 전용 신규 값은 `backend/app/core/config.py`
참고 (`CORS_ORIGINS`, `COOKIE_SECURE`, `AUTH_SESSION_TTL_DAYS`).

## 테스트

```bash
python -m pytest backend/tests/
```

## 알려진 한계

- 로그인 세션/채팅 세션 모두 프로세스 메모리 저장 - 서버 재시작 시 전부
  사라진다(기존 LangGraph `MemorySaver`와 동일한 한계, 의도적으로 일관되게
  맞춤).
- 실제 `data/vector_db`가 없는 환경에서는 채팅 API(`/api/v1/chat/*`)가
  503 `VECTOR_STORE_UNAVAILABLE`을 반환한다 - 회원가입/로그인/마이페이지 등
  나머지 API는 영향받지 않는다.
- S07-06/S10-01(구비서류를 항목별 칩으로 나열)은 옵션 ②로 확정(2026-09-16) -
  `detail.required_documents*`(원문 문자열)는 그대로 유지하면서, 백엔드가
  파싱한 `detail.required_documents*_items`(`string[] | null`)를 함께
  내려준다(`app/core/document_parsing.py` 참고).

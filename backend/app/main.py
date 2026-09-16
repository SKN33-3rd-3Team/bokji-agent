"""FastAPI 앱 진입점 - ``uvicorn backend.app.main:app --reload`` (레포 루트에서 실행)."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# src/rag_chatbot/service.py와 같은 이유로, 이 모듈이 어떻게 실행되든(uvicorn이
# CWD를 sys.path에 안 넣는 경우 포함) 레포 루트와 src/를 한 번만 방어적으로
# sys.path에 추가한다 - conftest.py가 pytest에서 하는 것과 동일한 부트스트랩.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import auth as auth_routes
from .api.v1 import chat as chat_routes
from .api.v1 import config as config_routes
from .api.v1 import users as users_routes
from .core.config import settings
from .core.errors import register_exception_handlers

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # service.get_graph()는 vectorDB 연결/LLM 클라이언트 구성 등 비용이 큰
    # 작업을 프로세스당 한 번만 하도록 캐싱한다(service.py 참고) - 여기서
    # 미리 호출해 첫 채팅 요청의 지연을 없앤다. 단, 로컬 개발 환경에는 실제
    # data/vector_db가 없을 수 있어(알려진 한계) 실패해도 서버 자체는
    # 계속 뜨게 한다 - 회원가입/로그인/마이페이지 등 채팅과 무관한 API는
    # 벡터DB 없이도 정상 동작해야 하기 때문이다. 채팅 API는 첫 호출 시
    # 다시 시도되며, 그때도 실패하면 503 VECTOR_STORE_UNAVAILABLE로 응답한다.
    try:
        from src.rag_chatbot.service import get_graph

        get_graph()
    except (Exception, SystemExit):  # noqa: BLE001 - SystemExit도 서버를 죽이면 안 됨(connect_store() 참고)
        _log.warning("get_graph() 워밍업 실패 - 채팅 API는 첫 요청 시 다시 시도됩니다.", exc_info=True)
    yield


app = FastAPI(title="bokji-agent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth_routes.router)
app.include_router(users_routes.router)
app.include_router(config_routes.router)
app.include_router(chat_routes.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}

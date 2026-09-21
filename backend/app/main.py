"""FastAPI 앱 진입점 - ``uvicorn backend.app.main:app --reload`` (레포 루트에서 실행)."""

from __future__ import annotations

import sys
import os
import threading
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path

# src/rag_chatbot/service.py와 같은 이유로, 이 모듈이 어떻게 실행되든(uvicorn이
# CWD를 sys.path에 안 넣는 경우 포함) 레포 루트와 src/를 한 번만 방어적으로
# sys.path에 추가한다 - conftest.py가 pytest에서 하는 것과 동일한 부트스트랩.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

_LOG_DIR = Path(os.environ.get("BOKJI_LOG_DIR") or _REPO_ROOT / "logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(_LOG_DIR / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ],
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import auth as auth_routes
from .api.v1 import chat as chat_routes
from .api.v1 import config as config_routes
from .api.v1 import users as users_routes
from .core.config import settings
from .core.errors import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 구동 즉시 워밍업을 시작한다. service.warm_up()은 get_graph()(vectorDB
    # 연결/LLM 클라이언트 구성)에 더해 **임베딩 모델 로드까지** 끝낸다 -
    # get_graph()만으로는 임베딩 모델이 "첫 인코딩 시점"까지 로드되지 않아,
    # 첫 질문이 들어왔을 때 검색 도중에 모델 로딩이 끼어든다(service.warm_up
    # docstring 참고).
    #
    # 워밍업은 별도 스레드에서 돌린다. 임베딩 모델 로딩은 수십 초가 걸릴 수
    # 있는데, 그동안 서버가 아예 연결을 못 받으면 회원가입/로그인 같은
    # 벡터DB와 무관한 화면까지 같이 멈춰 보인다. 진행 상태는
    # GET /api/v1/config/status로 화면이 직접 확인할 수 있다.
    #
    # 실패해도 서버는 계속 뜬다(로컬 개발 환경엔 실제 data/vector_db가 없을 수
    # 있다 - 알려진 한계). 채팅 API는 첫 호출 시 다시 시도하며, 그때도 실패하면
    # 503 VECTOR_STORE_UNAVAILABLE로 응답한다.
    from src.rag_chatbot.service import warm_up

    worker = threading.Thread(target=warm_up, name="bokji-warmup", daemon=True)
    # 테스트가 워밍업 완료를 기다릴 수 있게 스레드를 app.state에 남긴다.
    app.state.warmup_thread = worker
    worker.start()
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

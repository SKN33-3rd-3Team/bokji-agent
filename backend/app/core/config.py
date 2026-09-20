"""FastAPI 백엔드 설정.

기존 ``src/rag_chatbot``가 이미 ``.env``(레포 루트)를 ``load_dotenv``로
읽으므로(``service.py`` 참고), 여기서는 그 값을 다시 선언하지 않는다.
이 Settings는 FastAPI 계층에서만 필요한 신규 값(세션/쿠키/CORS)만 담는다.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # env_ignore_empty=True: 이 레포의 .env.example 관례("비워 두면 기본값")를
    # 그대로 따른다 - 없으면 COOKIE_SECURE=처럼 값을 비운 줄이 bool/int
    # 필드에서 빈 문자열 파싱 실패(ValidationError)로 앱 시작 자체를
    # 막아버린다(실측 확인됨).
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True
    )

    # 프론트 개발 서버 origin (쉼표로 여러 개 가능). 쿠키 기반 인증이라
    # allow_credentials=True와 함께 와일드카드 없이 명시적으로 지정해야 한다.
    cors_origins: str = "http://localhost:5173"

    # 로컬 http 개발 환경에서는 Secure 쿠키가 브라우저에 저장되지 않으므로
    # 기본은 False. 운영 배포(HTTPS) 시 반드시 true로 바꾼다.
    cookie_secure: bool = False

    # 로그인 세션(session_id 쿠키) 유효기간.
    auth_session_ttl_days: int = 7

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

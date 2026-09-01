"""PII 로깅 금지 규칙 (요구사항 6).

원칙
----
1. **원문 개인정보를 로그에 넘기지 않는다.** 비밀번호·이름·이메일·전화번호·
   주민등록번호 등은 로그 인자로 전달하지 않는다. 식별이 꼭 필요하면
   ``mask_email`` 등으로 마스킹한 값만 남긴다 (예: ``h***@example.com``).
2. **필터는 2차 방어선이다.** 그럼에도 실수로 흘러든 값을 잡기 위해, 이
   모듈이 만드는 로거에는 :class:`PiiRedactingFilter` 가 붙어 최종 메시지에서
   이메일·긴 숫자열을 가린다. 1번을 대신하지 않는다.
3. **auth 패키지는 반드시 :func:`get_auth_logger` 로 로거를 얻는다.**
   ``logging.getLogger(__name__)`` 를 직접 쓰지 않는다.

자세한 배경과 점검 항목은 ``docs/PII_LOGGING.md``.
"""

from __future__ import annotations

import logging
import re

# 이메일, 그리고 전화번호·주민등록번호처럼 길게 이어지는 숫자열.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_DIGITS_RE = re.compile(r"\d[\d\s.\-]{6,}\d")

_EMAIL_MASK = "[redacted-email]"
_NUMBER_MASK = "[redacted-number]"


def mask_email(value: object) -> str:
    """``hong@example.com`` -> ``h***@example.com``. 이메일이 아니면 ``***``."""

    if not isinstance(value, str) or "@" not in value:
        return mask_secret(value)
    local, _, domain = value.partition("@")
    head = local[0] if local else ""
    return f"{head}***@{domain}"


def mask_secret(value: object) -> str:
    """값의 존재 여부만 남기고 내용은 지운다."""

    return "***" if value not in (None, "") else ""


def redact(text: str) -> str:
    """문자열에서 이메일·긴 숫자열을 가린다 (필터 내부용, 직접 써도 무방)."""

    if not isinstance(text, str):
        return text
    text = _EMAIL_RE.sub(_EMAIL_MASK, text)
    text = _LONG_DIGITS_RE.sub(_NUMBER_MASK, text)
    return text


class PiiRedactingFilter(logging.Filter):
    """로그 레코드의 최종 메시지에서 PII 패턴을 지운다.

    ``record.getMessage()`` 로 포맷을 끝낸 뒤 치환하므로, ``%s`` 인자로
    들어온 값도 함께 가려진다. 레코드는 통과시킨다(반환값 항상 ``True``).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 포맷 실패 레코드는 건드리지 않는다
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_FILTER = PiiRedactingFilter()


def get_auth_logger(name: str = "rag_chatbot.auth") -> logging.Logger:
    """PII 리댁션 필터가 붙은 로거를 돌려준다."""

    logger = logging.getLogger(name)
    if not any(isinstance(f, PiiRedactingFilter) for f in logger.filters):
        logger.addFilter(_FILTER)
    return logger

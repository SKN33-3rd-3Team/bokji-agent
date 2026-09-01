"""비밀번호 정책 — 확정안: **8자 이상, 영문·숫자·특수문자 각 1자 이상**.

``validate_password`` 는 위반 사유를 한국어 문자열 리스트로 돌려준다(빈
리스트면 통과). 화면에서는 이 리스트를 그대로 사용자에게 보여주면 된다.
"""

from __future__ import annotations

MIN_LENGTH = 8
# bcrypt 앞에 SHA-256 프리해시를 두므로 길이 자체는 안전하지만, 비정상적으로
# 긴 입력은 애초에 거른다.
MAX_LENGTH = 1024


def validate_password(password: object) -> list[str]:
    """정책 위반 사유를 한국어로 모아 돌려준다. 통과하면 빈 리스트."""

    if not isinstance(password, str):
        return ["비밀번호를 문자열로 입력해 주세요."]

    violations: list[str] = []
    if len(password) < MIN_LENGTH:
        violations.append(f"비밀번호는 최소 {MIN_LENGTH}자 이상이어야 합니다.")
    if len(password) > MAX_LENGTH:
        violations.append(f"비밀번호는 {MAX_LENGTH}자 이하여야 합니다.")
    if not any(c.isascii() and c.isalpha() for c in password):
        violations.append("영문자를 최소 1자 포함해야 합니다.")
    if not any(c.isascii() and c.isdigit() for c in password):
        violations.append("숫자를 최소 1자 포함해야 합니다.")
    if not any((not c.isalnum()) and (not c.isspace()) for c in password):
        violations.append("특수문자를 최소 1자 포함해야 합니다.")
    return violations

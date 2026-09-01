"""``python -m rag_chatbot.auth keygen`` — 새 Fernet 암호화 키를 출력한다.

출력값을 ``.env`` 의 ``AUTH_ENC_KEY`` 에 넣는다. DB와 같은 곳에 두지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ``python src/rag_chatbot/auth/__main__.py`` 처럼 직접 실행해도 패키지를
# 찾도록 src/ 를 경로에 얹는다. ``python -m`` 으로 부를 땐 영향 없다.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rag_chatbot.auth.crypto import generate_key


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] != "keygen":
        print("usage: python -m rag_chatbot.auth keygen", file=sys.stderr)
        return 2
    print(generate_key())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

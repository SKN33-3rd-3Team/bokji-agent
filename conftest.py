"""pytest가 이 레포의 import 경로를 스스로 찾게 한다.

이 파일이 없으면 테스트를 돌릴 때마다 PYTHONPATH를 손으로 잡아야 하는데,
그 값이 셸마다 달라서 자주 깨진다:

- PowerShell:  $env:PYTHONPATH = ".;src"       (Windows 구분자는 세미콜론)
- Git Bash:    MSYS 경로 변환 때문에 세미콜론이 그대로 전달되지 않아
               src가 누락되고 ModuleNotFoundError가 난다(실제로 발생).

경로를 두 개 다 넣는 이유: 테스트가 두 형태로 import한다.

    from src.rag_chatbot...   -> 레포 루트가 sys.path에 있어야 함
    from rag_chatbot...       -> src/ 가 sys.path에 있어야 함

게다가 src 안에서도 절대 import를 쓰는 곳이 있다
(``claim_plan.py``의 ``from rag_chatbot.graph.nodes.law_source_resolver``).
그래서 둘 중 하나만 넣으면 반드시 어딘가 깨진다.

이제 어느 셸에서든 이것만으로 돌아간다:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

"""복지 에이전트 Streamlit 서비스 UI 패키지.

``app.py`` 가 얇은 진입점이고, 화면·파이프라인 배선·렌더링은 이 패키지의
하위 모듈에 나눠 둔다.

- ``constants``       : 한글 라벨 매핑, 선택지 목록 등 순수 상수
- ``vector_store``    : Chroma 벡터스토어 준비/샘플 색인 (``get_store``)
- ``claim_extractor`` : N5용 임시 규칙 기반 ClaimExtractor
- ``pipeline``        : N1~N12 그래프 배선 (``run_pipeline``)
- ``rendering``       : 파이프라인 결과 → Streamlit 위젯 렌더링
- ``theme``           : 헤더 로고, 우측 상단 메뉴 한글화
- ``session`` / ``nav``: 세션 상태 초기화, 화면 전환 헬퍼
- ``pages``           : 화면별 모듈 (chat / auth / mypage)

import 경로 부트스트랩
--------------------
노드 내부가 ``from rag_chatbot...`` / ``from rag_design...`` 절대 import 를
쓰므로, 이 패키지를 import 하는 것만으로 두 경로가 ``sys.path`` 에 얹히게 한다.
``rag_design`` 는 레포 루트, ``rag_chatbot`` 은 ``src/`` 아래에 있다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# streamlit_ui/ 의 부모가 레포 루트.
ROOT = Path(__file__).resolve().parent.parent

for _path in (ROOT, ROOT / "src"):
    _entry = str(_path)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

__all__ = ["ROOT"]

"""정책 상세 문의(light_followup) 평가셋의 '정책 컨텍스트'를 vectorDB에서 뽑아
동결한다.

평가셋은 두 조각으로 나뉜다:
- 이 스크립트가 만드는 ``light_followup_policies.json`` : 정책별 detail 섹션
  (vectorDB에서 ``_fetch_policy_detail``로 한 번만 뽑아 동결 - 평가는 LLM만
  변수로 두고 재현 가능해야 하므로).
- 손으로 쓰는 ``light_followup_dev.jsonl`` : 정책별 질문 + 기대 결과.

실행 (레포 루트, .venv):
    .venv\\Scripts\\python.exe scripts/build_light_followup_evalset.py
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

# 섹션 커버리지가 다양하도록 고른 정책들(구비서류 유/무, 지역 제한 유/무 섞음).
_POLICY_IDS = [
    "000000465790",  # 유아교육비(누리과정) 지원 - 구비서류 O
    "105100000001",  # 근로·자녀장려금 - 구비서류 O
    "116010000001",  # 청년 관련 자금보조 - 구비서류 O
    "119200000001",  # 친환경농업 직불제 - 구비서류 O
    "119200000076",  # 구비서류 X
    "119200000085",  # 구비서류 X
    "119200000118",  # 구비서류 X
    "119200000166",  # 구비서류 X
]


def main() -> None:
    from rag_chatbot.service import _fetch_policy_detail, connect_store

    store = connect_store()
    out: dict[str, dict] = {}
    for pid in _POLICY_IDS:
        detail = _fetch_policy_detail(pid, store, f"evalset-{pid}")
        sections = detail.get("sections") if "sections" in detail else detail
        meta = detail.get("meta", {})
        title = meta.get("title") or detail.get("title") or pid
        out[pid] = {
            "policy_id": pid,
            "title": title,
            "detail": {k: v for k, v in (sections or {}).items() if v},
        }
        print(f"{pid}  {title}")
        print(f"   섹션: {sorted(out[pid]['detail'].keys())}")

    dest = _ROOT / "data" / "evaluation" / "light_followup_policies.json"
    dest.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n동결: {dest}  ({len(out)}개 정책)")


if __name__ == "__main__":
    main()

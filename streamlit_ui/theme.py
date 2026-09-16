"""헤더 로고 + 우측 상단 메뉴 손질.

메뉴 손질 방침:
 1) '배포(Deploy)' 버튼만 숨긴다(점3개 ⋮ 메뉴는 유지).
 2) ⋮ 메뉴의 영문 문구를 한글로 바꾼다. Streamlit 은 이 메뉴의 로컬라이즈를
    공식 지원하지 않아, 열릴 때(지연 렌더) 텍스트 노드를 감시해 치환한다.
 3) 왼쪽 사이드바 상단 여백을 줄여 메뉴를 위로 당긴다.
Streamlit 버전이 오르면 selector/문구가 달라져 아래 값을 갱신해야 할 수 있다.
"""

from __future__ import annotations

import streamlit as st

_MENU_I18N_HTML = """
<style>
  [data-testid="stAppDeployButton"],
  [data-testid="stDeployButton"],
  .stAppDeployButton,
  .stDeployButton { display: none !important; }

  /* 사이드바 상단 여백 축소 — 메뉴를 위로.
     stSidebarHeader 는 고정 height + marginBottom 으로 빈 공간을 잡으므로
     그 둘을 없애는 게 핵심(padding 만 건드리면 안 움직인다). */
  [data-testid="stSidebarHeader"] {
    height: auto !important;
    min-height: 0 !important;
    margin-bottom: 0 !important;
    padding-top: .25rem !important;
    padding-bottom: 0 !important;
  }
  [data-testid="stSidebarUserContent"] {
    padding-top: 0 !important;
  }

  /* 사이드바 요소 간격 압축 — 스크롤 없이 한 화면에 */
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: .5rem !important;
  }
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {
    margin-bottom: .1rem !important;
  }
  section[data-testid="stSidebar"] h3 {          /* st.subheader("설정") */
    margin: .1rem 0 .35rem !important;
  }
  section[data-testid="stSidebar"] .stButton button {
    padding-top: .35rem !important;
    padding-bottom: .35rem !important;
    min-height: 0 !important;
  }
</style>
<script>
(function () {
  if (window.__bokjiMenuI18n) return;
  window.__bokjiMenuI18n = true;
  const MAP = {
    "System": "시스템", "Light": "라이트", "Dark": "다크",
    "Rerun": "다시 실행", "Always rerun": "항상 다시 실행",
    "Auto rerun": "자동 다시 실행", "Clear cache": "캐시 지우기",
    "Print": "인쇄", "Record a screencast": "화면 녹화 시작",
    "Record screen": "화면 녹화", "Stop recording": "화면 녹화 중지",
    "About": "정보", "Settings": "설정", "Get help": "도움말",
    "Report a bug": "버그 신고", "Deploy": "배포",
    "Developer options": "개발자 옵션", "Wide mode": "와이드 모드"
  };
  function translate(root) {
    if (!root || root.nodeType !== 1) root = document.body;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const hits = [];
    while (w.nextNode()) hits.push(w.currentNode);
    for (const n of hits) {
      const raw = n.nodeValue;
      if (!raw) continue;
      const t = raw.trim();
      if (!t) continue;
      if (Object.prototype.hasOwnProperty.call(MAP, t)) {
        n.nodeValue = raw.replace(t, MAP[t]);
      } else if (/^Made with Streamlit\\b/i.test(t)) {
        n.nodeValue = "";
      }
    }
  }
  new MutationObserver(function (muts) {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) translate(node);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
  translate(document.body);
  let n = 0;
  const iv = setInterval(function () {
    translate(document.body);
    if (++n > 20) clearInterval(iv);
  }, 500);
})();
</script>
"""


def localize_menu() -> None:
    st.html(_MENU_I18N_HTML, unsafe_allow_javascript=True)


# 타이틀 로고: 말풍선 + 하트(대화형 복지 상담).
# st.html 은 DOMPurify(USE_PROFILES:{html:true})가 <svg>를 지워서 못 쓴다.
# st.image 는 SVG 문자열을 정식 지원하므로, 가로 컨테이너에 [로고][타이틀]로
# 나란히 놓는다. 색은 활성 테마에 맞춰 서버에서 골라 넣는다(테마 전환 시 rerun).
_LOGO_D1 = (
    "M6 8.6C6 6.6 7.6 5 9.7 5h12.6C24.4 5 26 6.6 26 8.6V17c0 2-1.6 3.6-3.7 "
    "3.6H13l-4.6 4.1c-.7.6-1.7.1-1.7-.8v-3.3H9.7C7.6 20.6 6 19 6 17z"
)
_LOGO_D2 = (
    "M16 17.7s-4.1-2.7-4.1-5.7c0-1.6 1.2-2.6 2.5-2.6.9 0 1.5.5 1.6 1.3.1-.8.7"
    "-1.3 1.6-1.3 1.3 0 2.5 1 2.5 2.6 0 3-4.1 5.7-4.1 5.7Z"
)


def _logo_svg(is_dark: bool) -> str:
    brand = "#8B87F0" if is_dark else "#4F46E5"
    soft = "#332F63" if is_dark else "#E7E5FB"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
        'width="40" height="40">'
        f'<path d="{_LOGO_D1}" fill="{soft}" stroke="{brand}" stroke-width="2" '
        'stroke-linejoin="round"/>'
        f'<path d="{_LOGO_D2}" fill="{brand}"/></svg>'
    )


# 프로필 사진 - 실제로 업로드한 사진이 있으면 그걸 쓰고(mypage._profile_edit_form
# 참고), 없으면 이름 이니셜로 만든 원형 아바타로 대신한다(Slack/Gmail류 기본
# 아바타 패턴). 이름마다 항상 같은 색이 나오게 문자 코드값 합을 팔레트
# 인덱스로 쓴다(진짜 랜덤이면 새로고침마다 색이 바뀐다).
_AVATAR_PALETTE = (
    "#4F46E5", "#059669", "#D97706", "#DC2626", "#7C3AED", "#2563EB", "#DB2777",
)


def avatar_svg(name: str, *, size: int = 28) -> str:
    initial = _esc_svg_text((name or "?").strip()[:1] or "?")
    color = _AVATAR_PALETTE[sum(ord(c) for c in name) % len(_AVATAR_PALETTE)] if name else _AVATAR_PALETTE[0]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        'viewBox="0 0 32 32">'
        f'<circle cx="16" cy="16" r="16" fill="{color}"/>'
        '<text x="16" y="21.5" font-family="\'Noto Sans KR\',sans-serif" font-size="15" '
        f'font-weight="700" fill="#fff" text-anchor="middle">{initial}</text></svg>'
    )


def _esc_svg_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_avatar(container, *, name: str, size: int = 28) -> None:
    """아바타 하나를 그린다 - 이름 이니셜이 든 원.

    호출부(사이드바 계정 칸·마이페이지 카드)가 매번 이니셜·색상 계산을
    직접 하지 않도록 여기 한 곳에 모은다 - 나중에 표시 로직이 바뀌어도
    이 함수만 고치면 된다.

    (사진 업로드는 아직 없다 - 별도로 다룰 예정.)
    """

    container.image(avatar_svg(name, size=size), width=size)


_HEADER_ROW_KEY = "app_header_row"


def render_header() -> None:
    try:
        is_dark = getattr(st.context.theme, "type", "light") == "dark"
    except Exception:  # noqa: BLE001 - 컨텍스트 없으면 라이트로
        is_dark = False

    border = "#2A2F3C" if is_dark else "#E4E6EF"
    # 로고+타이틀은 왼쪽, 로그인 상태는 오른쪽 - 컨테이너 자체엔 정렬 옵션이
    # 없어서 st-key 클래스에 CSS로 직접 space-between을 건다(rendering.py
    # 비교 바에서 쓰는 것과 같은 방식). 아래쪽 테두리 + 여백도 같이 줘서
    # "그냥 제목 텍스트"가 아니라 앱 바(topbar)처럼 보이게 한다.
    st.html(
        f"<style>[class*='st-key-{_HEADER_ROW_KEY}']{{justify-content:space-between;"
        f"padding-bottom:14px;margin-bottom:6px;border-bottom:1px solid {border};}}</style>"
    )
    row = st.container(horizontal=True, vertical_alignment="center", key=_HEADER_ROW_KEY)
    brand = row.container(horizontal=True, vertical_alignment="center")
    brand.image(_logo_svg(is_dark), width=40)
    brand.title("복지 에이전트")

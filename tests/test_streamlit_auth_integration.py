"""app.py 를 실제로 실행해 로그인/회원가입/마이페이지 연동을 검증한다.

``AppTest`` 로 ``app.py`` 를 구동하되 ``view`` 를 chat 이외로 고정해 무거운
벡터스토어 로딩을 건너뛴다. 인증 백엔드(SQLite+bcrypt+Fernet)는 임시 DB와
테스트용 키로 실제 동작시킨다.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from rag_chatbot.auth import authenticate
from rag_chatbot.auth.crypto import generate_key

_APP = str(Path(__file__).resolve().parents[1] / "app.py")
_PW = "Abcd1234!"
_PW2 = "Zyxw9876$"


class StreamlitAuthIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "auth.db")
        os.environ["AUTH_DB_PATH"] = self._db
        os.environ["AUTH_ENC_KEY"] = generate_key()

    def tearDown(self):
        os.environ.pop("AUTH_DB_PATH", None)
        os.environ.pop("AUTH_ENC_KEY", None)
        self._tmp.cleanup()

    # -- 공통 헬퍼 --------------------------------------------------------
    def _app(self, view: str) -> AppTest:
        at = AppTest.from_file(_APP, default_timeout=30)
        at.session_state["view"] = view
        return at.run()

    @staticmethod
    def _click(at: AppTest, label: str) -> AppTest:
        for btn in at.button:
            if btn.label == label:
                btn.click()
                return at.run()
        raise AssertionError(
            f"button {label!r} not found; have {[b.label for b in at.button]}"
        )

    def _signup(self, at: AppTest, *, email, pw, pw2=None, name="김복지",
                tos=True, privacy=True, region=None, interests=None):
        at.text_input(key="su_email").set_value(email)
        at.text_input(key="su_pw").set_value(pw)
        at.text_input(key="su_pw2").set_value(pw2 if pw2 is not None else pw)
        at.text_input(key="su_name").set_value(name)
        at.checkbox(key="su_tos").set_value(tos)
        at.checkbox(key="su_privacy").set_value(privacy)
        if region is not None:
            at.selectbox(key="su_region").set_value(region)
        if interests is not None:
            at.pills(key="su_interests").set_value(interests)
        return self._click(at, "회원가입")

    def _login(self, at: AppTest, *, email, pw):
        at.text_input(key="login_email").set_value(email)
        at.text_input(key="login_pw").set_value(pw)
        return self._click(at, "로그인")

    def _register_and_login(self, *, email, pw=_PW, name="김복지", region=None,
                            interests=None) -> AppTest:
        self._signup(self._app("signup"), email=email, pw=pw, name=name,
                     region=region, interests=interests)
        at = self._login(self._app("login"), email=email, pw=pw)
        at.session_state["view"] = "mypage"
        return at.run()

    # -- 회원가입 -------------------------------------------------------------
    def test_signup_page_renders_without_exception(self):
        at = self._app("signup")
        self.assertFalse(at.exception)
        self.assertTrue(any("회원가입" in m.value for m in at.markdown))

    def test_login_page_has_single_signup_button(self):
        at = self._app("login")
        labels = [b.label for b in at.button]
        self.assertEqual(labels.count("회원가입"), 1)   # 인라인 링크 하나만
        self.assertEqual(labels.count("로그인"), 1)     # 폼 제출 버튼
        self.assertIn("상담으로 돌아가기", labels)

    def test_signup_page_has_single_login_button(self):
        at = self._app("signup")
        labels = [b.label for b in at.button]
        self.assertEqual(labels.count("로그인"), 1)      # 인라인 링크 하나만
        self.assertEqual(labels.count("회원가입"), 1)    # 폼 제출 버튼
        self.assertIn("상담으로 돌아가기", labels)

    def test_signup_success_moves_to_login(self):
        at = self._signup(self._app("signup"), email="new@example.com", pw=_PW)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "login")
        self.assertFalse(at.error)

    def test_signup_success_clears_form_state(self):
        at = self._signup(self._app("signup"), email="clr@example.com", pw=_PW,
                          name="지울이")
        for key in ("su_email", "su_name", "su_pw", "su_pw2", "su_tos", "su_privacy"):
            self.assertNotIn(key, at.session_state)

    def test_signup_password_policy_shown(self):
        at = self._signup(self._app("signup"), email="weak@example.com",
                          pw="weak", pw2="weak")
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "signup")
        self.assertIn("8자", " ".join(e.value for e in at.error))

    def test_signup_password_mismatch(self):
        at = self._signup(self._app("signup"), email="mm@example.com",
                          pw=_PW, pw2="Zzzz9999$")
        self.assertTrue(any("일치" in e.value for e in at.error))

    def test_signup_requires_consent(self):
        at = self._signup(self._app("signup"), email="nc@example.com",
                          pw=_PW, privacy=False)
        self.assertTrue(any("동의" in e.value for e in at.error))

    def test_signup_duplicate(self):
        self._signup(self._app("signup"), email="dup@example.com", pw=_PW)
        at2 = self._signup(self._app("signup"), email="dup@example.com", pw=_PW)
        self.assertTrue(any("이미 가입" in e.value for e in at2.error))

    # -- 로그인 -------------------------------------------------------------
    def test_login_success_sets_session_and_goes_chat(self):
        self._signup(self._app("signup"), email="ok@example.com", pw=_PW, name="복지왕")
        at = self._login(self._app("login"), email="ok@example.com", pw=_PW)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "chat")
        user = at.session_state["auth_user"]
        self.assertEqual(user["username"], "ok@example.com")
        self.assertEqual(user["display_name"], "복지왕")
        self.assertNotIn("login_pw", at.session_state)

    def test_login_wrong_password_unified_message(self):
        self._signup(self._app("signup"), email="wp@example.com", pw=_PW)
        at = self._login(self._app("login"), email="wp@example.com", pw="Nope9999$")
        self.assertFalse(at.exception)
        self.assertIsNone(at.session_state["auth_user"])
        self.assertTrue(any("이메일 또는 비밀번호" in e.value for e in at.error))

    def test_login_unknown_user_same_message(self):
        at = self._login(self._app("login"), email="ghost@example.com", pw=_PW)
        self.assertTrue(any("이메일 또는 비밀번호" in e.value for e in at.error))

    # -- 마이페이지: 표시 -------------------------------------------------
    def test_mypage_requires_login(self):
        at = self._app("mypage")
        self.assertFalse(at.exception)
        self.assertTrue(any("로그인이 필요" in i.value for i in at.info))

    def test_mypage_shows_logged_in_user(self):
        at = self._register_and_login(email="mp@example.com", name="마이유저")
        self.assertFalse(at.exception)
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("마이유저", blob)
        self.assertIn("mp@example.com", " ".join(c.value for c in at.caption))

    def test_mypage_shows_signup_profile(self):
        at = self._register_and_login(
            email="prof@example.com", name="프로필유저",
            region="서울특별시", interests=["장애인", "청년"],
        )
        self.assertFalse(at.exception)
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("서울특별시", blob)
        self.assertIn("장애인", blob)
        self.assertIn("청년", blob)
        self.assertEqual(at.session_state["auth_user"]["region"], "서울특별시")
        self.assertEqual(
            set(at.session_state["auth_user"]["interests"]), {"장애인", "청년"}
        )

    def test_mypage_name_with_markdown_is_escaped(self):
        at = self._register_and_login(email="md@example.com",
                                      name="# 큰제목 [링크](http://evil)")
        self.assertFalse(at.exception)
        blob = " ".join(m.value for m in at.markdown)
        # escape_md 를 거쳐 링크/헤딩 문법이 무력화된다
        self.assertIn(r"\[링크\]", blob)
        self.assertNotIn("[링크](http://evil)", blob)

    def test_cross_user_form_state_reset_on_logout(self):
        self._signup(self._app("signup"), email="usera@example.com", pw=_PW, name="에이")
        self._signup(self._app("signup"), email="userb@example.com", pw=_PW, name="비이")

        at = self._login(self._app("login"), email="usera@example.com", pw=_PW)
        at.session_state["view"] = "mypage"
        at = at.run()
        self.assertEqual(at.session_state["pe_name"], "에이")

        at = self._click(at, "로그아웃")
        self.assertNotIn("pe_name", at.session_state)
        self.assertNotIn("_mp_forms_user", at.session_state)

        at.session_state["view"] = "login"
        at = at.run()
        at = self._login(at, email="userb@example.com", pw=_PW)
        at.session_state["view"] = "mypage"
        at = at.run()
        self.assertEqual(at.session_state["pe_name"], "비이")  # 에이 값 잔존 아님

    # -- 마이페이지: 비밀번호 변경 연동 --------------------------------
    def test_mypage_password_change_success(self):
        at = self._register_and_login(email="pc@example.com")
        at.text_input(key="pc_cur").set_value(_PW)
        at.text_input(key="pc_new1").set_value(_PW2)
        at.text_input(key="pc_new2").set_value(_PW2)
        at = self._click(at, "비밀번호 변경")
        self.assertFalse(at.exception)
        # 새 비밀번호로 로그인되고 옛 비밀번호는 거부된다.
        self.assertEqual(
            authenticate("pc@example.com", _PW2, db_path=self._db).username,
            "pc@example.com",
        )
        with self.assertRaises(Exception):
            authenticate("pc@example.com", _PW, db_path=self._db)

    def test_mypage_password_change_wrong_current(self):
        at = self._register_and_login(email="pcw@example.com")
        at.text_input(key="pc_cur").set_value("Wrong123!")
        at.text_input(key="pc_new1").set_value(_PW2)
        at.text_input(key="pc_new2").set_value(_PW2)
        at = self._click(at, "비밀번호 변경")
        self.assertFalse(at.exception)
        self.assertTrue(any("현재 비밀번호" in e.value for e in at.error))

    def test_mypage_password_change_policy(self):
        at = self._register_and_login(email="pcp@example.com")
        at.text_input(key="pc_cur").set_value(_PW)
        at.text_input(key="pc_new1").set_value("weak")
        at.text_input(key="pc_new2").set_value("weak")
        at = self._click(at, "비밀번호 변경")
        self.assertFalse(at.exception)
        self.assertIn("8자", " ".join(e.value for e in at.error))

    def test_mypage_password_change_mismatch(self):
        at = self._register_and_login(email="pcm@example.com")
        at.text_input(key="pc_cur").set_value(_PW)
        at.text_input(key="pc_new1").set_value(_PW2)
        at.text_input(key="pc_new2").set_value("Other999$")
        at = self._click(at, "비밀번호 변경")
        self.assertTrue(any("일치" in e.value for e in at.error))

    # -- 마이페이지: 기본 정보 수정 연동 -----------------------------
    def test_mypage_profile_edit(self):
        at = self._register_and_login(
            email="pe@example.com", name="원래이름",
            region="부산광역시", interests=["청년"],
        )
        at.text_input(key="pe_name").set_value("바뀐이름")
        at.selectbox(key="pe_region").set_value("인천광역시")
        at.pills(key="pe_interests").set_value(["장애인", "노인/어르신"])
        at = self._click(at, "저장")
        self.assertFalse(at.exception)
        user = at.session_state["auth_user"]
        self.assertEqual(user["display_name"], "바뀐이름")
        self.assertEqual(user["region"], "인천광역시")
        self.assertEqual(set(user["interests"]), {"장애인", "노인/어르신"})
        # 재조회(다음 렌더)에도 유지
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("바뀐이름", blob)
        self.assertIn("인천광역시", blob)

    # -- 마이페이지: 회원 탈퇴 연동 ---------------------------------
    def test_mypage_delete_account_success(self):
        at = self._register_and_login(email="del@example.com", name="탈퇴유저")
        at.text_input(key="da_pw").set_value(_PW)
        at.checkbox(key="da_agree").set_value(True)
        at = self._click(at, "회원 탈퇴")
        self.assertFalse(at.exception)
        self.assertIsNone(at.session_state["auth_user"])
        self.assertEqual(at.session_state["view"], "chat")
        with self.assertRaises(Exception):
            authenticate("del@example.com", _PW, db_path=self._db)

    def test_mypage_delete_account_wrong_password(self):
        at = self._register_and_login(email="delw@example.com")
        at.text_input(key="da_pw").set_value("Wrong123!")
        at.checkbox(key="da_agree").set_value(True)
        at = self._click(at, "회원 탈퇴")
        self.assertFalse(at.exception)
        self.assertTrue(any("비밀번호가 올바르지 않" in e.value for e in at.error))
        self.assertIsNotNone(at.session_state["auth_user"])
        self.assertEqual(
            authenticate("delw@example.com", _PW, db_path=self._db).username,
            "delw@example.com",
        )

    def test_mypage_delete_account_requires_agree(self):
        at = self._register_and_login(email="dela@example.com")
        at.text_input(key="da_pw").set_value(_PW)
        at = self._click(at, "회원 탈퇴")
        self.assertFalse(at.exception)
        self.assertTrue(any("탈퇴 동의" in e.value for e in at.error))
        self.assertIsNotNone(at.session_state["auth_user"])

    # -- 로그아웃 -------------------------------------------------------
    def test_logout_clears_session(self):
        at = self._register_and_login(email="lo@example.com")
        at = self._click(at, "로그아웃")
        self.assertFalse(at.exception)
        self.assertIsNone(at.session_state["auth_user"])
        self.assertEqual(at.session_state["view"], "chat")

    def test_logout_clears_conversation_state(self):
        # 공용 PC: 로그아웃 시 이전 사용자의 상담 내역·민감 슬롯이 남으면 안 된다.
        at = self._register_and_login(email="conv@example.com")
        at.session_state["messages"] = [
            {"role": "user", "content": "월 소득 300만원, 장애 있음"}
        ]
        at.session_state["slots"] = {"income_bracket": "median_60", "disability": True}
        at.session_state["slot_ask_counts"] = {"region": 1}
        at.session_state["pending_prompt"] = "이어서 물어봐"
        at = at.run()

        at = self._click(at, "로그아웃")
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["messages"], [])
        self.assertEqual(at.session_state["slots"], {})
        self.assertEqual(at.session_state["slot_ask_counts"], {})
        # chat 페이지가 렌더 중 pending_prompt 를 소비(pop)하므로 falsy 만 확인.
        self.assertFalse(
            "pending_prompt" in at.session_state
            and at.session_state["pending_prompt"]
        )

    def test_login_clears_previous_users_conversation(self):
        self._signup(self._app("signup"), email="ua@example.com", pw=_PW, name="에이")
        self._signup(self._app("signup"), email="ub@example.com", pw=_PW, name="비이")

        at = self._login(self._app("login"), email="ua@example.com", pw=_PW)
        at.session_state["messages"] = [{"role": "user", "content": "에이의 상담"}]
        at.session_state["slots"] = {"disability": True}
        at = at.run()

        at.session_state["view"] = "login"
        at = at.run()
        at = self._login(at, email="ub@example.com", pw=_PW)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["messages"], [])
        self.assertEqual(at.session_state["slots"], {})


if __name__ == "__main__":
    unittest.main()

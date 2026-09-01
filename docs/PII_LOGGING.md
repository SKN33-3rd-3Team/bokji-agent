# PII 로깅 금지 규칙

회원 인증 기능이 다루는 개인정보(PII)를 로그·디버그 출력·예외 메시지에
남기지 않기 위한 규칙이다. 코드로는 `src/rag_chatbot/auth/pii_logging.py` 가
강제한다.

## 원칙

1. **원문 PII를 로그 인자로 넘기지 않는다.**
   - 대상: 비밀번호, 비밀번호 해시/암호문, 이름, 이메일, 전화번호,
     생년월일, 주민등록번호, 주소 상세, 관심 지원조건(장애·보훈·기초수급
     등 민감 범주가 섞일 수 있음).
   - 식별이 꼭 필요하면 마스킹 값만 남긴다: `mask_email("hong@example.com")`
     → `h***@example.com`, 그 외 값은 `mask_secret()` → `***`.
   - 이벤트만 남긴다. 예: `login ok username=h***@example.com`,
     `signup fail (policy) username=h***@example.com`.

2. **필터는 2차 방어선이다.**
   - `get_auth_logger()` 로 만든 로거에는 `PiiRedactingFilter` 가 붙어
     최종 메시지에서 이메일·긴 숫자열을 `[redacted-*]` 로 치환한다.
   - 이 필터에 의존해서 원문을 넘기지 않는다. 1번이 먼저다.

3. **auth 패키지는 `get_auth_logger()` 로만 로거를 얻는다.**
   `logging.getLogger(__name__)` 직접 호출 금지.

4. **예외 메시지에도 PII를 넣지 않는다.**
   `AuthError` 계열 메시지는 사용자에게 그대로 보여도 되는 일반 문구만
   담는다(입력값 echo 금지).

5. **Streamlit 디버그 패널.**
   `st.session_state["auth_user"]` 에는 복호화된 표시이름이 들어간다.
   디버그 출력에 세션 상태 전체를 덤프하지 않는다(`chat.py` 의 debug 모드
   확장 시 `auth_user` 를 제외한다).

## 점검 항목 (auth 관련 PR 리뷰 시)

- [ ] 새 로그 호출이 아이디를 `mask_email()` 없이 넘기지 않는가
- [ ] 비밀번호/해시/암호문/이름을 로그·예외·주석 예시에 넣지 않았는가
- [ ] 새 모듈이 `get_auth_logger()` 를 쓰는가
- [ ] 세션 상태·요청 바디를 통째로 로깅하는 코드가 없는가

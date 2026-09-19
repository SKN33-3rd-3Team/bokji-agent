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

5. **화면·HTTP 디버그 출력.**
   FastAPI 요청 바디·쿠키·프로필 응답과 React 상태를 통째로 덤프하지 않는다. 레거시 Streamlit의 `st.session_state["auth_user"]`에도 복호화된 표시이름이 있으므로 전체 세션 덤프에서 제외한다. 요구사항 S05-04의 `output_*`·`llm_status`·`timing`은 D12 결정에 따라 현재 데모 노출을 유지한다. 새 관리자 권한·승자 provider 필드를 추가한 것은 아니다. 진단 필드 유지가 토큰·쿠키·프로필·프롬프트·원시 모델 출력의 로그 허용을 뜻하지 않는다. [백엔드 계약 추적표](../backend/README.md#원본-문서와-남은-계약-차이)를 참고한다.

6. **RunPod 실패 로그는 안전한 식별자만 남긴다.**
   [LLM 클라이언트](../src/rag_chatbot/llm/client.py)는 직접 호출·폴백 시도의 실패를 각 시도에서 한 번 기록한다. HTTP 401/403은 `auth_failure`, 나머지는 `server_failure` 그룹이다. 상태 코드와 제공된 짧은 오류 code/type(없으면 예외 유형)만 남기며 메시지·응답 본문을 덤프하지 않는다. HF가 성공해도 이 로그는 남는다. 인증 로거 필터가 모든 LLM 로그에 적용된다고 가정하지 않는다. 상담 정리 실패도 원래 오류를 가리지 않는 정적 경고로 처리한다.

## 점검 항목 (auth 관련 PR 리뷰 시)

- [ ] 새 로그 호출이 아이디를 `mask_email()` 없이 넘기지 않는가
- [ ] 비밀번호/해시/암호문/이름을 로그·예외·주석 예시에 넣지 않았는가
- [ ] 새 모듈이 `get_auth_logger()` 를 쓰는가
- [ ] 세션 상태·요청 바디를 통째로 로깅하는 코드가 없는가

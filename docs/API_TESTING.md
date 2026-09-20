# 오프라인 FastAPI API 테스트 실행 설명서

회원 DB 계정이나 LLM 키 없이 API-01~14의 HTTP 계약을 검사한다. 실제 인증·쿠키 처리와 임시 SQLite를 사용하며, 채팅의 외부 검색·모델 호출은 기존 테스트의 고정 응답 또는 메모리 그래프로 대체한다.

## 1. 처음 한 번 준비하기 — Windows PowerShell

저장소를 받은 뒤 `backend`, `scripts` 폴더가 보이는 **프로젝트 루트**에서 PowerShell을 연다. 저장소 권장 버전은 **64비트 Python 3.11**이며 CI도 3.11을 사용한다. 아래 명령의 `python`이 설치한 Python인지 먼저 확인한다. 의존성 설치에는 인터넷과 충분한 디스크 공간이 필요하다. 기존 백엔드 의존성을 재사용하므로 임베딩용 패키지도 설치되지만 테스트는 모델을 다운로드하지 않는다.

```powershell
python --version
python -m venv .venv-api
& .\.venv-api\Scripts\python.exe -m pip --isolated install -r backend/requirements-test.txt
if ($LASTEXITCODE -ne 0) { throw "테스트 의존성 설치 실패" }
```

가상환경 활성화나 PowerShell 실행 정책 변경은 필요 없다. 이후에는 항상 아래 가상환경의 Python을 직접 실행한다. `.env`를 복사하거나 DB·RunPod·Hugging Face 키를 입력하지 않는다. `.venv-api/`와 테스트 보고서는 Git에서 제외된다.

## 2. 전체 검사 — 한 명령

```powershell
& .\.venv-api\Scripts\python.exe scripts/run_api_tests.py
$LASTEXITCODE
```

실행기는 앱을 import하기 전에 다음을 적용한다.

- 상속받은 DB·LLM 자격증명, `PYTEST_ADDOPTS`, `PYTEST_PLUGINS` 등을 제거하고 외부 pytest 플러그인 자동 로드를 끈다. 부모 PowerShell의 환경변수는 바꾸지 않는다.
- 실행마다 임시 디렉터리·SQLite 기본 경로·임시 암호화 키를 만든다. 기존 fixture가 테스트마다 별도 SQLite DB와 새 키를 사용하고 로그인·상담 세션 메모리를 비운다.
- dotenv 로딩을 무효화하고 `.env`/`.env.*` 파일 접근을 차단한다. 네트워크 연결·DNS·외부 프로세스 실행과 임시 경로 밖 SQLite 접근을 차단한다. Windows asyncio 내부 socketpair는 허용한다.
- 의도하지 않은 I/O는 `OfflineAccessError`로 테스트를 실패시킨다. 앱의 일반 오류 폴백이 이를 정상 응답으로 숨기지 못하게 한다. 파일 쓰기는 임시 데이터와 보고서 경로로 제한한다.

기존 fixture의 mock을 사용하므로 앱 워밍업도 실제 모델을 준비하지 않는다. pytest의 결과 코드를 그대로 반환하며 종료 시 임시 데이터는 정리한다. 테스트 코드를 신뢰하고 실행하기 위한 보호 장치이며 별도의 OS 컨테이너는 아니다.

## 3. 파일·시나리오 선택

```powershell
# 인증 API만
& .\.venv-api\Scripts\python.exe scripts/run_api_tests.py backend/tests/test_auth_api.py

# 자동 추천 API만
& .\.venv-api\Scripts\python.exe scripts/run_api_tests.py backend/tests/test_auto_recommendation.py

# 이름에 signup이 들어가는 검사, 개별 이름 표시
& .\.venv-api\Scripts\python.exe scripts/run_api_tests.py -k signup -v

# 실패한 상담의 새 세션 안내 회귀 하나만
& .\.venv-api\Scripts\python.exe scripts/run_api_tests.py backend/tests/test_chat_turns.py::test_http_completed_turn_ownership_and_failure_mapping
```

`-x`를 붙이면 첫 실패에서 멈춘다. 실행기는 `backend/tests` 아래의 경로·pytest node ID만 받는다. 다른 pytest 인자를 그대로 전달하는 범용 실행기는 아니다. 위 선택 실행도 전체 실행과 같은 보고서 파일을 **덮어쓴다**.

## 4. 결과와 실패 확인

콘솔 끝의 `passed`, `failed`, `errors`, `skipped`, `deselected`, `warnings`를 구분한다. 일부 선택으로 빠진 `deselected`나 경고를 통과 수에 더하지 않는다. 실제 DB·LLM 연동은 이 검사 대상에 없으며 “skip으로 검증된 것”도 아니다.

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 선택된 테스트 통과 |
| 1 | 테스트 실패 또는 시작 오류 (콘솔 traceback 확인) |
| 2 | 중단/수집 오류 또는 잘못된 실행기 인자 |
| 3 | pytest 내부 오류 |
| 4 | pytest 사용 오류 |
| 5 | 선택된 테스트 없음 (`-k`/경로 확인) |

프로젝트 루트 기준 보고서:

- `reports/api-tests/junit.xml`: 테스트별 결과·소요 시간·실패 내용. CI에서도 같은 파일을 업로드한다.
- `reports/api-tests/pytest.log`: pytest가 수집한 로그. 콘솔의 요약·traceback과 함께 확인한다.

```powershell
# JUnit 전체 집계
[xml]$report = Get-Content -LiteralPath .\reports\api-tests\junit.xml -Raw
$report.testsuites.testsuite | Select-Object tests, failures, errors, skipped, time
Get-Content -LiteralPath .\reports\api-tests\pytest.log -Tail 30
```

유효한 테스트 실행을 시작하면 의존성을 import하기 전에 기존 `junit.xml`과 `pytest.log` 두 파일만 삭제한다. 같은 폴더의 다른 파일은 보존한다. 따라서 dotenv/pytest import 등 시작 단계에서 실패하면 이전 성공 보고서가 남지 않으며, 새 JUnit도 생성되지 않을 수 있다. 삭제 권한 오류는 무시하지 않고 실행을 중단하므로, 이때 남은 파일을 이번 실행 결과로 판단하지 않는다.

`--help`, 실행기가 거절한 잘못된 인자·검사 범위 밖 경로는 테스트 시작 전 종료하므로 기존 보고서를 보존한다. 의존성 설치 명령 역시 보고서를 갱신하지 않는다. 콘솔 종료 코드·오류와 보고서 수정 시각을 함께 확인하고, 실패를 무시한 채 다음 단계로 진행하지 않는다.

## 5. API별 검사 위치

아래 경로는 모두 저장소 기준이며 **검사 대상과 근거**다. 실제 서버·제공자의 성공을 뜻하지 않는다.

| API | 주요 검사 | 기존 테스트 |
| --- | --- | --- |
| 01 가입 | 필수 동의·공개 선택지·쿠키·중복 가입 | [test_auth_api.py](../backend/tests/test_auth_api.py) |
| 02 로그인 | 성공·잘못된 암호·탈퇴 경합·세션 발급 | [test_auth_api.py](../backend/tests/test_auth_api.py), [test_password_session_revocation.py](../backend/tests/test_password_session_revocation.py) |
| 03 로그아웃 | 멱등 응답 | [test_auth_api.py](../backend/tests/test_auth_api.py) |
| 04 내 정보 | 인증·프로필·회원 ID·no-store | [test_auth_api.py](../backend/tests/test_auth_api.py), [test_auth_identity.py](../backend/tests/test_auth_identity.py) |
| 05 프로필 수정 | 부분 수정·null 지우기·날짜/선택지 | [test_auth_api.py](../backend/tests/test_auth_api.py), [test_birth_date_contract.py](../backend/tests/test_birth_date_contract.py) |
| 06 비밀번호 | 실패 보존·현재 세션 유지·다른 세션 폐기/경합 | [test_password_session_revocation.py](../backend/tests/test_password_session_revocation.py) |
| 07 탈퇴 | 암호 확인·토큰/상담 정리·진행 중 요청 | [test_account_chat_cleanup.py](../backend/tests/test_account_chat_cleanup.py) |
| 08 채팅 기본값 | 저장 프로필의 known 값 변환 | [test_auth_api.py](../backend/tests/test_auth_api.py) |
| 09 검색 옵션 | 공개 접근·선택지 | [test_auth_api.py](../backend/tests/test_auth_api.py) |
| 10 상담 시작 | 요청 검증·고정 응답·503/500·실패 정리 | [test_chat_api.py](../backend/tests/test_chat_api.py), [test_chat_session_lifecycle.py](../backend/tests/test_chat_session_lifecycle.py) |
| 11 상담 재개 | interrupt·새 턴·실패 후 새 상담 안내·계산 입력 | [test_chat_turns.py](../backend/tests/test_chat_turns.py), [test_calc_followup.py](../backend/tests/test_calc_followup.py) |
| 12 정책 문의 | 세션 소유권·캐시 내 정책·고정 답변 | [test_chat_api.py](../backend/tests/test_chat_api.py) |
| 13 상담 삭제 | 소유권 경계·멱등 삭제·실행/삭제 경합 | [test_chat_api.py](../backend/tests/test_chat_api.py), [test_chat_session_lifecycle.py](../backend/tests/test_chat_session_lifecycle.py) |
| 14 자동 추천 | 서버 프로필·입력 금지·정상 0건/오류 구분·no-store·탈퇴 | [test_auto_recommendation.py](../backend/tests/test_auto_recommendation.py) |

추가로 [앱/CORS/워밍업](../backend/tests/test_app.py), [축소 시간의 노드 한도](../backend/tests/test_node_deadline.py), [실행기 격리 경계](../backend/tests/test_api_test_runner.py)를 검사한다. 오류 코드·미결 사항은 [백엔드 계약](../backend/README.md)을 따른다.

## 6. 반복 실행·문제 해결

- **반복 실행:** 2절 명령을 다시 실행한다. DB·키·세션 데이터는 새로 시작하며 기존 회원 DB를 지우지 않는다. 가상환경은 재사용한다. 중단된 프로세스가 남았다면 종료한 뒤 다시 실행한다.
- **Python 명령 없음/스토어 창 열림:** 64비트 Python 3.11 설치와 PATH를 확인한다. Python Launcher가 설치돼 있다면 준비 명령의 `python -m venv` 대신 `py -3.11 -m venv`를 사용할 수 있다.
- **ModuleNotFoundError:** `.venv-api`의 Python으로 1절 설치 명령을 재실행한다. 루트 `requirements.txt`의 Streamlit 전체 설치는 필요 없다.
- **설치 다운로드 실패:** 인터넷·프록시·디스크 공간을 확인한다. 이는 API 테스트 실패나 서비스 장애 판정이 아니다. 패키지 설치 후 테스트 실행에는 인터넷이 필요 없다.
- **OfflineAccessError:** 테스트가 mock 없이 외부 호출·실데이터 접근을 시도했는지 확인한다. 보호 장치를 끄거나 실제 키를 넣어 통과시키지 않는다. 원인·명령·종료 코드와 보고서를 공유한다.
- **FAILED/ERROR:** 출력된 파일/node ID로 3절처럼 좁혀 실행한다. 실제 코드 결함, 테스트 fixture 결함, 설치/환경 오류를 구분하고 기존 실패를 보존한다.
- **보고서 정리:** 필요할 때 `reports/api-tests/junit.xml`과 `pytest.log` 두 생성 파일만 지운다. 다음 실행에서 다시 생성된다. 보고서에 인증 키·쿠키·실제 개인정보를 추가하지 않는다.

## 7. PR 자동 검사와 검증 한계

로컬 작성 검증은 Windows의 새 가상환경 **Python 3.14.7**에서 수행했다. 전체 명령 결과는 **220 passed, 0 failed, 0 skipped**, 기존 의존성 경고 14건(Starlette 1건·LangSmith 13건)이었으며, 의존성 검사 `python -m pip check`도 통과했다. Python 3.11/Linux의 GitHub 실행 결과는 아직 없으므로 이 로컬 결과와 구분한다. 이후 코드·의존성이 바뀌면 다시 실행한 결과를 기준으로 판단한다.

[api-tests.yml](../.github/workflows/api-tests.yml)은 PR과 수동 `workflow_dispatch`에서 Python 3.11을 설치하고 **동일한 `python scripts/run_api_tests.py`**를 실행한다. `contents: read`, checkout 자격증명 비보존, 20분 제한이며 서비스 비밀값을 요구하지 않는다. 실패해도 JUnit을 `api-tests-junit` artifact로 업로드한다. 설치 단계에서 실패해 보고서가 없으면 경고만 남기되 앞선 실패 상태는 유지한다. 이 로컬 작업은 workflow를 업로드하거나 GitHub 실행을 시작하지 않는다.

현재 상태는 CI 파일 준비 완료이며 온라인 실행은 미검증이다. PR 검사는 push 후 PR의 `opened`/`synchronize`/`reopened` 이벤트에서 실행될 수 있지만 저장소 승인·필터·충돌 조건의 영향을 받는다. 수동 **Run workflow** 버튼은 workflow 파일이 기본 브랜치(`main`)에 등록된 뒤 사용할 수 있다. PR62 브랜치에만 파일이 있거나 로컬에만 준비된 상태에서는 수동 버튼을 보장하지 않는다. 근거: [수동 실행 조건](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow), [PR 이벤트 조건](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).

TestClient는 같은 프로세스의 ASGI 앱을 호출한다. TCP 서버, 실제 브라우저/React, TLS·프록시·배포 설정을 검증하지 않는다. SQLite는 원격 MySQL/MariaDB의 연결·권한·트랜잭션 호환성 증명이 아니다. 외부 검색/LLM은 고정 fixture이며 실제 제공자의 품질·요금·90초 동작도 미검증이다. 원격 DB/실제 제공자는 별도 테스트 DB·권한·키·네트워크 및 비용 승인을 준비한 다음 별도 단계에서 검증해야 한다. 이번 실행기에 실환경 전환 옵션은 없다.

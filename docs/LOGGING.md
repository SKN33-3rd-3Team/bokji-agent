# 로그 위치

FastAPI를 실행하면 백엔드 로그는 저장소 루트의 `logs/backend.log`에 기록됩니다.
파일은 5MB마다 `backend.log.1`부터 최대 3개까지 순환하며, `logs/`는 Git에
커밋하지 않습니다. 표준 출력에도 같은 로그가 표시됩니다.

`BOKJI_LOG_DIR` 환경변수로 저장 폴더를 바꿀 수 있습니다. 미설정 시 기존
`logs/` 경로를 사용합니다. 오프라인 테스트 실행기는 임시 폴더를 지정하며,
테스트 종료 시 해당 로그는 삭제됩니다. pytest 로그는 `reports/api-tests/pytest.log`에 남습니다.

HuggingFace 호출 실패는 모델명, 추출된 HTTP 상태코드(확인되지 않으면
`unknown`), 진단 메시지와 예외 traceback을 함께 기록합니다. 토큰과 요청
본문은 기록하지 않습니다.

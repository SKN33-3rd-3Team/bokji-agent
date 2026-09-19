-- dev_account01 계정 생성
-- 대상: skn33.iptime.org에 새로 옮긴 회원가입/인증용 DB 서버(RunPod 아님 -
-- RunPod 연동은 별도로 제거 예정).
-- 호스트는 '%'(모든 호스트 허용) - 팀원들이 유동 IP로 접속해야 해서 특정
-- IP로 좁히지 못함. 대신 DB 서버가 있는 공유기/네트워크에서 3306(또는
-- 실제 사용 포트)을 필요한 곳에서만 접근 가능하도록 포트포워딩·방화벽을
-- 제한하고, 비밀번호를 충분히 강력하게 유지하는 것으로 보완한다.
-- 비밀번호는 이 파일에 평문으로 남기지 않는다 - 실행 전 셸에서
-- DEV_DB_PASSWORD를 채운 뒤 envsubst로 치환해서 실행한다:
--   DEV_DB_PASSWORD='<실제 비밀번호>' envsubst < scripts/setup_sing_up_db.sql | mysql -h skn33.iptime.org -P <port> -u root -p
CREATE USER 'dev_account01'@'%' IDENTIFIED BY '${DEV_DB_PASSWORD}';

-- bokji_auth DB에 한정된 권한만 부여 (전체 DB 관리자 권한 금지)
GRANT ALL PRIVILEGES ON bokji_auth.* TO 'dev_account01'@'%';
FLUSH PRIVILEGES;

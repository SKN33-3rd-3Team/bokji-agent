#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# RunPod 일반 Ubuntu Pod 에서 회원 DB(MariaDB)를 자동으로 올린다.
#
# 언제 쓰나: `mariadb:11` 이미지로 Pod 를 다시 배포하기 어렵고, 이미 띄워 둔
# Ubuntu/PyTorch Pod 를 그대로 써야 할 때. (권장은 여전히 mariadb:11 이미지 —
# docs/AUTH_REMOTE_DB.md 1번. 그게 되면 이 스크립트는 필요 없다.)
#
# 무엇을 보장하나 (멱등 — 여러 번 돌려도 안전):
#   1. MariaDB 미설치면 apt 로 설치
#   2. 외부 접속 허용 (bind-address = 0.0.0.0)
#   3. 데이터 디렉터리를 /workspace/mariadb 에 둠  ← Pod 재시작에도 보존되는
#      영속 볼륨. (컨테이너 디스크 /var/lib/mysql 은 Stop 시 초기화되므로 피한다.)
#   4. 서버 기동 (systemd 없는 컨테이너 환경 대응)
#   5. DB / 앱 계정 생성·비밀번호 동기화
#
# 사용법 A) Pod 접속 후 수동:
#     MARIADB_PASSWORD=... bash scripts/runpod_mariadb_bootstrap.sh
#
# 사용법 B) Pod 설정의 "Container Start Command" 에 등록해 자동화:
#     bash -c 'bash /workspace/runpod_mariadb_bootstrap.sh; exec sleep infinity'
#   (스크립트를 /workspace 에 복사해 두어야 재시작에도 남는다. Start Command 를
#    바꾸면 템플릿에 따라 SSH 가 안 뜰 수 있는데, 그때는 RunPod 웹 터미널을
#    쓰거나 `/start.sh & ` 를 앞에 붙인다.)
#
# 환경변수 (Pod 의 Environment Variables 로 넣어두면 A/B 모두 자동 적용):
#   MARIADB_PASSWORD  (필수)  앱 계정 비밀번호
#   MARIADB_DATABASE  (기본 bokji)      생성할 DB 이름
#   MARIADB_USER      (기본 bokji_app)  앱 계정 이름
#   MARIADB_DATADIR   (기본 /workspace/mariadb)  영속 데이터 위치
# ---------------------------------------------------------------------------
set -euo pipefail

DB_NAME="${MARIADB_DATABASE:-bokji}"
DB_USER="${MARIADB_USER:-bokji_app}"
DB_PASS="${MARIADB_PASSWORD:?MARIADB_PASSWORD 환경변수를 설정하세요}"
DATADIR="${MARIADB_DATADIR:-/workspace/mariadb}"
SOCK=/run/mysqld/mysqld.sock

export DEBIAN_FRONTEND=noninteractive
log() { echo "[bootstrap] $*"; }

# 1) 설치 -----------------------------------------------------------------
if ! command -v mariadbd >/dev/null 2>&1 && ! command -v mysqld >/dev/null 2>&1; then
  log "MariaDB 미설치 — apt 로 설치 (수십 초)"
  apt-get update -qq
  apt-get install -y -qq mariadb-server iproute2
else
  log "MariaDB 이미 설치됨"
fi
MARIADBD="$(command -v mariadbd || command -v mysqld)"

# 2) 외부 접속 허용 ------------------------------------------------------
for f in /etc/mysql/mariadb.conf.d/50-server.cnf /etc/mysql/my.cnf \
         /etc/my.cnf /etc/mysql/mysql.conf.d/mysqld.cnf; do
  [ -f "$f" ] || continue
  sed -i 's/^\s*bind-address.*/bind-address = 0.0.0.0/' "$f" || true
  sed -i 's/^\s*skip-networking/# skip-networking/' "$f" || true
done

# 3) 영속 데이터 디렉터리 ----------------------------------------------
mkdir -p "$DATADIR"
chown -R mysql:mysql "$DATADIR"
if [ ! -d "$DATADIR/mysql" ]; then
  log "데이터 디렉터리 초기화: $DATADIR"
  mariadb-install-db --user=mysql --datadir="$DATADIR" >/dev/null
fi

# 4) 기동 (이미 3306 리스닝 중이면 건너뜀) ---------------------------
mkdir -p /run/mysqld && chown mysql:mysql /run/mysqld
if ss -tlnp 2>/dev/null | grep -qE ':3306(\s|$)'; then
  log "이미 3306 리스닝 중"
else
  log "mariadbd 기동"
  nohup "$MARIADBD" --user=mysql --datadir="$DATADIR" \
        --bind-address=0.0.0.0 --socket="$SOCK" \
        >/var/log/mariadbd.log 2>&1 &
  for _ in $(seq 1 30); do
    mysqladmin --socket="$SOCK" ping >/dev/null 2>&1 && break
    sleep 1
  done
fi
mysqladmin --socket="$SOCK" ping >/dev/null 2>&1 || {
  log "기동 실패 — /var/log/mariadbd.log 확인"; tail -n 20 /var/log/mariadbd.log; exit 1;
}

# 5) DB / 계정 보장 ---------------------------------------------------
log "DB '$DB_NAME' / 계정 '$DB_USER' 보장"
mysql --socket="$SOCK" <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%';
FLUSH PRIVILEGES;
SQL

log "완료. 리스닝 상태:"
ss -tlnp | grep 3306 || true
log "AUTH_DB_URL=mysql://${DB_USER}:<password>@<이 Pod의 3306 매핑 IP:포트>/${DB_NAME}"

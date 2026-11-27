# 공개 관리자 서버 배포

`hs.officeworkkit.com`의 UI와 기존 FastAPI 서버를 같은 출처로 제공한다. 별도 관리자 회원가입 없이 `ADMIN_ID`·`ADMIN_PW` 한 쌍으로 로그인한다. PostgreSQL 문서 목록·PDF 업로드·다운로드·삭제는 실제 저장소를 사용한다. 이 구성은 모델을 설치하거나 RAG를 주입하지 않으므로 `/query`·`/conflict/`와 readiness `/health`는 503을 반환한다. 컨테이너 생존 점검은 `/health/live`를 사용한다. 현재 UI의 설정 저장은 비활성 상태이며 이 배포로 활성화되지 않는다.

## 구성과 경계

| 구성 | 연결·데이터 | 공개 범위 |
|---|---|---|
| Cloudflare Tunnel | `front`의 `172.30.83.10`; 토큰 파일을 읽기 전용으로 마운트 | 공개 호스트를 `http://web:8080`으로 전달 |
| Nginx | `front`·`backend`, backend IP `172.30.82.10` | UI 파일과 명시한 API 경로만 제공 |
| FastAPI | `backend`·내부 `data`; UID 65532 | 호스트 포트 없음, 앱 DB 계정만 사용 |
| PostgreSQL 16/pgvector | 내부 `data` 전용, `pgdata` 볼륨 | 호스트 포트 없음 |
| 업로드 | 별도 `uploads` 볼륨, UID/GID 65532·0700 | 정적 폴더에 포함하지 않음 |

`front`는 `172.30.83.0/24`, `backend`는 `172.30.82.0/24`다. 배포 전에 호스트의 Docker·VPN·LAN 대역과 충돌하지 않는지 확인한다. API의 향후 외부 모델 연결을 위해 `backend`는 일반 bridge이며, DB의 `data`는 internal 네트워크다. Tunnel 컨테이너는 `front`에만 연결하므로 API·DB에 직접 연결할 수 없다. 기존 포트폴리오·Caddy·다른 Compose 프로젝트의 설정은 변경하지 않는다.

Nginx는 Tunnel 커넥터 IP에서 받은 `CF-Connecting-IP`만 신뢰하고 `X-Forwarded-For`를 덮어쓴다. Uvicorn도 Nginx의 고정 IP만 신뢰한다. 따라서 로그인 제한기는 공개 요청의 실제 IP를 사용한다. `X-Forwarded-Proto: http`는 HTTPS로 308 전환하며, HTTPS 요청은 반복 전환하지 않는다. 헤더 없는 loopback HTTP 점검은 허용한다. API·Nginx 접근 로그는 비활성이고, 초기화 실패 시 DB 연결 문자열을 출력하지 않는다.

Nginx는 Docker DNS(`127.0.0.11`) 조회 결과를 10초간 캐시하고 다시 조회해 API 컨테이너의 주소 변경을 반영한다. 프록시는 원래 URI·쿼리 문자열·요청 본문을 전달하며, `/documents/{id}` 메타데이터와 `/documents/{id}/file` 원본 경로를 모두 허용한다. 고정 이름의 UI 자산이 재배포 뒤 오래 남지 않도록 모든 응답에 `Cache-Control: no-store`를 추가한다.

## 비밀값 준비

1. `deploy/.env.example`을 저장소 밖의 전용 파일(예: `/srv/hsrule-private/production.env`)로 복사하고 권한을 `0600`으로 제한한다. 실제 비밀번호와 키를 채운다. 예제의 `replace-with-...` 값은 운영 자격 증명이 아니다.
2. `POSTGRES_PASSWORD`와 `DB_ADMIN_URL`의 소유자 비밀번호를 일치시키고, `DB_APP_PASSWORD`와 `DATABASE_URL`의 `hsrule_web` 비밀번호를 일치시킨다. URL 비밀번호의 특수문자는 percent encoding한다. Compose env 파일의 따옴표·`$` 처리도 확인한다. `SECRET_KEY`는 무작위 32자 이상으로 만든다.
3. `PUBLIC_ORIGIN=https://hs.officeworkkit.com`을 명시한다. 앱 기본 origin에 의존하지 않는다. `HSRULE_TUNNEL_TOKEN_FILE`은 절대 경로로 지정한다. Tunnel을 시작하기 전에는 파일이 없어도 된다.

정상 API에는 `DATABASE_URL`·관리자 자격 증명·키·origin·업로드 경로만 전달된다. `DB_ADMIN_URL`·`DB_APP_PASSWORD`는 `setup` 프로필의 일회성 `bootstrap` 서비스만, DB 소유자 비밀번호는 DB 서비스만 받는다. env 파일 전체를 컨테이너에 전달하거나 이미지에 복사하지 않는다. 비밀값이 출력되는 `docker compose config` 대신 `config --quiet`을 사용한다.

## 최초 기동

호스트에는 Docker Engine과 Compose가 필요하다. 아래 명령은 저장소 루트에서 실행한다.

```sh
ENV_FILE=/srv/hsrule-private/production.env
dc() { docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"; }

dc config --quiet
dc build api web
dc up -d --wait db
dc --profile setup run --rm bootstrap
dc up -d --wait api web
curl --fail --silent http://127.0.0.1:18081/health/live
curl --fail --silent http://127.0.0.1:18081/rules
```

`init-uploads`는 네트워크와 비밀값 없이 볼륨 최상위 폴더의 UID/GID·권한만 초기화하고 종료한다. `bootstrap`은 `hsrule_web` 역할이 없을 때만 안전한 SQL 식별자와 바인딩된 비밀번호로 생성한다. 기존 `DocumentStore.initialize()`·`grant_app_role()`로 migration과 행 권한을 적용하며, 재실행해도 기존 역할 비밀번호를 변경하지 않는다. 일반 API는 migration을 실행하지 않는다.

로그인 후 빈 DB에서는 업로드 규정 목록이 빈 배열로 표시되어야 한다. 운영 확인은 관리자 로그인·비인증 거부·PDF 업로드·다운로드·컨테이너 재생성 후 보존·삭제를 포함한다. 관리자 비밀번호나 Bearer 토큰을 명령 인자·로그·스크린샷에 남기지 않는다. `/health`의 503은 현재 모델 미연결 상태에서 예상한 결과이며 DB 점검은 아래 명령으로 따로 수행한다.

```sh
dc exec -T api python -m hsrule_web.storage_check --verify-hash
```

## 공개 호스트 연결

Cloudflare에 이 서비스 전용 Tunnel을 만들고 공개 hostname `hs.officeworkkit.com`의 서비스 URL을 `http://web:8080`으로 지정한다. 다른 도메인의 Tunnel이나 기존 Caddy를 변경하지 않는다. 기존 정적 Worker 라우트·DNS 값은 먼저 기록해 둔다.

Tunnel 토큰은 Git 밖의 파일에 저장한다. 파일은 컨테이너 UID 1000이 읽을 수 있어야 하므로 호스트에서 소유자를 `1000:1000`, 모드를 `0400`으로 설정하고, 부모 폴더의 접근 권한도 제한한다. 파일 내용과 토큰이 든 실행 명령은 출력하지 않는다. `HSRULE_TUNNEL_TOKEN_FILE` 경로를 일치시킨 뒤에만 실행한다.

```sh
dc --profile tunnel up -d --wait tunnel
```

Cloudflare에서 이 호스트를 가로채는 기존 Worker route를 해제하고 Tunnel DNS로 전환한다. 기존 Worker는 즉시 삭제하지 않아 이전 경로로 복구할 수 있게 한다. 공개 HTTPS에서 UI·로그인·저장소 동작과 HTTP→HTTPS 전환을 확인한다. Tunnel 시작 전에는 loopback `127.0.0.1:18081`만 접근할 수 있다. API·DB의 공개 포트나 호스트 80·443 포트는 추가하지 않는다.

## 재배포와 관리자 변경

배포 소스와 이미지 태그를 기록하고, 아래 백업을 만든 뒤 새 이미지를 빌드한다. `HSRULE_IMAGE_TAG`는 이전 이미지와 구분되는 값으로 지정한다. 외부 기본 이미지는 검증한 플랫폼 digest로 고정할 수 있다(`HSRULE_POSTGRES_IMAGE`, `HSRULE_CLOUDFLARED_IMAGE`; 빌드의 `PYTHON_IMAGE`, `NGINX_IMAGE` 인자).

```sh
dc build api web
dc --profile setup run --rm bootstrap
dc up -d --wait api web
dc exec -T api python -m hsrule_web.storage_check --verify-hash
```

관리자 ID·비밀번호는 private env 파일에서 변경한 뒤 `dc up -d --wait --force-recreate api`로 반영한다. 단순 `restart`는 변경된 환경변수를 읽지 않는다. 기존 JWT까지 무효화해야 하면 `SECRET_KEY`도 교체한다. DB 비밀번호 변경은 별도 DBA 작업이다. bootstrap은 기존 역할의 비밀번호를 덮어쓰지 않으므로 env 값만 바꿔서는 DB 비밀번호가 변경되지 않는다.

## 백업과 복원

기존 [문서 저장소 운영](document-store-ops.md)의 같은 시점 백업 도구를 사용한다. 백업에는 문서 메타데이터·페이지 행과 원본 PDF가 함께 들어간다. 관리자 env와 Tunnel 토큰은 별도 비밀 저장소에 보관한다. 아래 `bootstrap` 서비스 명령 덮어쓰기는 DB 소유자 연결 문자열을 프로세스 인자에 노출하지 않는다.

```sh
sudo install -d -m 0700 -o 65532 -g 65532 /srv/hsrule-backups
BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"
dc --profile setup run --rm -v /srv/hsrule-backups:/backups bootstrap \
  python -c 'import os,sys; from pathlib import Path; from hsrule_web.backup import create_backup; create_backup(os.environ["DB_ADMIN_URL"], Path(os.environ["UPLOAD_DIR"]), Path(sys.argv[1]))' \
  "/backups/$BACKUP_ID"
dc --profile setup run --rm -v /srv/hsrule-backups:/backups:ro bootstrap \
  python -m hsrule_web.backup verify "/backups/$BACKUP_ID"
```

백업을 호스트 밖의 제한된 저장소에도 복사하고 주기적으로 빈 DB·빈 업로드 볼륨에서 복원을 연습한다. 이 도구는 웹 문서 테이블만 다루므로 향후 RAG 색인을 연결하면 DB 전체 `pg_dump` 백업도 별도로 추가한다.

복원은 기존 서비스 정지 후 기존 볼륨을 보존한 상태에서 **새 이름의 빈 `pgdata`·`uploads` 볼륨**으로 진행한다. 두 볼륨의 `name`만 바꾸는 private Compose override를 추가해 사용한다. 고정 서브넷이 겹치므로 기존 프로젝트 네트워크를 남긴 채 두 번째 프로젝트를 동시에 기동하지 않는다. `docker compose down`은 볼륨을 유지하며 `down -v`는 사용하지 않는다. 새 DB를 기동한 뒤 아래 명령으로 복원하고 bootstrap으로 역할·권한을 부여한 후 API·web을 시작한다.

```sh
# dc 함수에 검토한 private 빈 볼륨 override를 추가한 상태에서 실행한다.
dc up -d --wait db
dc --profile setup run --rm -v /srv/hsrule-backups:/backups:ro bootstrap \
  python -c 'import os,sys; from pathlib import Path; from hsrule_web.backup import restore_backup; restore_backup(Path(sys.argv[1]), os.environ["DB_ADMIN_URL"], Path(os.environ["UPLOAD_DIR"]))' \
  "/backups/$BACKUP_ID"
dc --profile setup run --rm bootstrap
dc up -d --wait api web
dc exec -T api python -m hsrule_web.storage_check --verify-hash
```

## 롤백과 패키징 점검

UI·API만 되돌릴 때는 기록한 소스/이미지 태그로 돌아가 `dc up -d --wait --no-build api web`을 실행한다. DB migration 호환 여부를 먼저 확인한다. 호환되지 않으면 기존 볼륨을 덮어쓰지 않고 위 백업을 새 볼륨으로 복원한다. 공개 경로 장애는 Cloudflare의 기록한 Worker route·DNS로 되돌리고 Tunnel을 정지한다. 정적 UI로 되돌리는 동안 관리자 API 기능은 제공되지 않는다.

빌드 컨텍스트는 `.dockerignore`의 파일 허용 목록으로 제한한다. API 이미지에는 웹 서버에 필요한 Python 파일·migration·기존 HTML/로고만, 공개 Nginx 이미지에는 `scripts/build_ui_site.py`가 만든 정적 UI만 복사한다. `.env`·ZIP·데이터·테스트·로그·백엔드 소스·`_headers`·`_redirects`는 공개 이미지에 포함하지 않는다. Nginx는 목록 밖의 URL에 404를 반환한다.

```sh
# 예제값으로 구문만 점검하며 컨테이너는 시작하지 않는다.
docker compose --env-file deploy/.env.example -f compose.production.yaml config --quiet
docker compose --env-file deploy/.env.example -f compose.production.yaml build api web
# hsrule_bootstrap_test라는 폐기 가능한 DB와 새 역할 공간에서만 실행한다.
HSRULE_BOOTSTRAP_TEST_DSN='<private disposable test DSN>' \
  python -m unittest discover -s deploy -p test_bootstrap.py
```

초기화 회귀 점검은 비어 있는 env의 거부, 특수문자 비밀번호의 안전한 생성, 두 번째 실행 시 비밀번호 유지, 앱 역할의 행 접근과 스키마 생성 금지를 실제 PostgreSQL에서 검증한다.

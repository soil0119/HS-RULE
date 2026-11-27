# 웹 문서 저장소 운영 (#21)

`web_documents`·`web_document_pages`(DB)와 `UPLOAD_DIR`의 원본 PDF를 함께 운영하는 절차다. 공식 규정 색인(`hs_reg_*`)은 [rag-index-db.md](rag-index-db.md)를 따른다.

> **적용 범위.** 아래 절차와 리허설은 로컬 Docker의 PostgreSQL 16.16(`pgvector/pgvector:pg16`)과 macOS 파일 시스템에서만 실행했다. **실제 운영 DB와 운영 서버의 업로드 폴더에는 적용하지 않았다.** 운영 적용 전에 같은 리허설을 운영과 같은 버전·권한 구성에서 다시 한다. 미검증 범위는 [맨 아래](#미검증-범위)에 정리했다.

## 1. 초기화·migration·앱 계정

두 계정을 나눈다.

| 계정 | 쓰는 곳 | 권한 |
|---|---|---|
| 소유자(`DATABASE_URL` of migration·백업) | `python -m hsrule_web.documents`, `hsrule_web.backup` | 테이블 생성·변경, `EXCLUSIVE` 잠금 |
| 앱 계정(서버의 `DATABASE_URL`) | `uvicorn server:app`, `hsrule_web.storage_check` | 행 읽기·쓰기만 |

```sh
# (DBA) 앱 로그인 계정을 만든다. 비밀번호는 비밀 저장소에만 둔다.
psql "$OWNER_URL" -c "CREATE ROLE hsrule_app LOGIN PASSWORD '...'"
# (소유자) migration을 적용하고 앱 계정에 행 권한을 준다. 여러 번 실행해도 안전하다.
DATABASE_URL="$OWNER_URL" python -m hsrule_web.documents --grant-role hsrule_app
# 서버는 앱 계정으로 실행한다.
DATABASE_URL="$APP_URL" uvicorn server:app ...
```

- migration 파일은 `hsrule_web/migrations/NNNN_*.sql`이고, #24의 비파괴 러너(`hsrule.migrations`)로 적용한다. 이력은 `web_migrations`에 따로 남는다(`hs_reg_migrations`와 섞이지 않음). 적용된 파일을 고치면 체크섬 불일치로 거부되고, `DROP`·`TRUNCATE`·`DELETE FROM`이 있으면 적용 전에 거부된다.
- **기존 DB의 기준선 채택.** PR #33의 인라인 `SCHEMA`로 만든 DB에서 처음 실행하면 `0001_web_documents.sql`(같은 `CREATE TABLE IF NOT EXISTS` 문장)이 아무것도 바꾸지 않고 이력에만 기록된다. 행·파일은 그대로다(`test_pre_migration_database_adopts_the_baseline_without_changing_data`).
- 여러 프로세스가 동시에 실행해도 advisory lock으로 직렬화되어 한 번만 적용된다.
- 서버는 시작할 때 migration을 실행하지 않는다. 배포 순서는 **migration → 권한 부여 → 서버 재시작**이다.

앱 계정에 주는 권한과 이유:

| 테이블 | 권한 | 이유 |
|---|---|---|
| `web_documents` | `SELECT, INSERT, UPDATE, DELETE` | `UPDATE`는 행을 바꾸려는 것이 아니라 `SELECT … FOR UPDATE`(삭제)·`FOR SHARE`(본문 조회)에 필요하다. 빼면 본문 조회가 실패하는 것을 확인했다. |
| `web_document_pages` | `SELECT, INSERT` | 페이지 삭제는 `ON DELETE CASCADE`가 테이블 소유자 권한으로 처리한다. |
| `web_migrations` | 없음 | 앱은 이력을 읽거나 쓰지 않는다. |

앱 계정은 `CREATE`·`DROP`·`TRUNCATE`·이력 조회가 거부된다(`test_cli_migrates_and_grants_an_app_role_row_access_only`). 새 DB의 `public` 스키마에서는 `REVOKE CREATE ON SCHEMA public FROM PUBLIC`도 함께 한다(PostgreSQL 15부터 기본값).

## 2. 동시 처리·무결성 회귀

| 상황 | 기대 동작 | 테스트 |
|---|---|---|
| COMMIT 응답 유실 | 원본 보존, 복구 연결 실패 시에도 파일 유지 | `test_web_documents`: `test_pdf_survives_a_lost_commit_acknowledgement` (PR #33) |
| COMMIT 시점 거부 | 원본 제거, 행 없음 | `test_proven_commit_rollback_removes_the_uncommitted_original` (PR #33) |
| 삭제 중 DB·파일 실패 | 원본·행 복원 | `test_database_delete_failure_restores_original_file` 외 (PR #33) |
| 삭제 재시도 경합 | 복구가 끝날 때까지 재시도 대기 | `test_delete_serializes_a_retry_with_filesystem_recovery_after_commit_failure` (PR #33) |
| 같은 PDF 동시 업로드 4건 | 1건만 저장, 나머지 `409 duplicate_document`, 파일 1개 | `test_web_document_ops`: `test_concurrent_uploads_of_the_same_pdf_store_exactly_one` |
| 미확정 중복 행과 경합 | 상대 트랜잭션을 기다렸다가 COMMIT이면 409, ROLLBACK이면 저장 | `test_upload_waits_for_an_uncommitted_duplicate_and_follows_its_outcome` |
| 삭제 COMMIT 전 같은 PDF 재업로드 | 삭제 완료까지 대기 후 새 ID로 저장, 옛 파일·`.deleting` 없음 | `test_reupload_during_delete_waits_and_succeeds_after_the_delete_commits` |
| 삭제 실패 중 재업로드 | 재업로드 409, 원본·행 복원 | `test_reupload_during_a_failed_delete_is_rejected_and_the_original_is_restored` |
| 다운로드 시작 후 삭제 | 열린 파일로 끝까지 전송, 이후 404, 잔여 파일 없음 | `test_download_in_progress_finishes_when_the_document_is_deleted` |
| 메타데이터 조회와 파일 열기 사이 삭제 | 404 (빈 200이 아님) | `test_delete_between_metadata_lookup_and_file_open_returns_404` |

DB 테스트는 격리된 PostgreSQL이 있을 때만 실행된다. 테스트마다 임시 스키마·역할을 만들고 지운다.

```sh
docker compose up -d --wait db
docker compose exec db psql -U postgres -c "CREATE DATABASE hsrule_test"
HSRULE_WEB_TEST_DSN=postgresql://postgres:<pw>@127.0.0.1:5432/hsrule_test python -m unittest discover -s tests
```

## 3. 읽기 전용 점검

```sh
DATABASE_URL="$APP_URL" UPLOAD_DIR=/srv/hsrule/uploads python -m hsrule_web.storage_check --verify-hash
```

DB와 폴더를 비교만 하고 아무것도 고치지 않는다. 세션은 read-only 트랜잭션이라 쓰기 문장은 DB가 거부한다. 문서마다 `save`·`delete`와 같은 advisory lock을 잡고 다시 읽으므로, 진행 중인 업로드(파일은 있고 행은 아직 COMMIT 전)를 불일치로 보고하지 않는다. 잠금이 5초 안에 풀리지 않으면 `busy`로 보고한다. 종료 코드는 0 정상, 1 불일치, 2 점검 불가다. `--json`으로 기계가 읽을 수 있다. `--verify-hash`는 모든 원본을 읽으므로 한가한 시간에 실행한다.

### 수동 복구 절차

공통 원칙:
1. 고치기 전에 `hsrule_web.backup create`로 현재 상태를 백업한다(원본 누락이 있으면 거부되므로 그때는 `UPLOAD_DIR`을 통째로 복사해 둔다).
2. 파일은 지우지 않고 업로드 폴더 밖의 격리 폴더(예: `/srv/hsrule/quarantine/<날짜>/`)로 옮긴다. 보관 기간이 지나면 지운다.
3. 행은 SQL로 직접 고치지 않고 API(`DELETE /uploaded-rules/{id}`, 재업로드)로 처리한다.
4. 고친 뒤 점검을 다시 실행해 0으로 끝나는지 확인한다.

| 종류 | 의미·주요 원인 | 조치 |
|---|---|---|
| `recoverable_original` | 행은 있는데 원본이 `.<id>.pdf.deleting`으로만 남았다. 삭제 실패 후 복구 연결도 실패했다. | 관리자가 삭제하려던 문서면 API로 다시 삭제한다(삭제가 남은 사본을 마저 지운다). 지우려던 것이 아니면 `.deleting`의 SHA-256이 `content_hash`와 같은지 확인하고 `<id>.pdf`로 이름을 바꾼다. |
| `missing_original` | 행은 있는데 원본이 어디에도 없다. | 최근 백업의 `manifest.json` `files`에서 `<id>.pdf`를 찾아 해시가 `content_hash`와 같으면 복사한다. 백업에 없으면 API로 삭제하고 원본 PDF를 다시 업로드한다. |
| `unregistered_file` | DB에 없는 `<id>.pdf`. 저장 COMMIT 결과를 확인하지 못한 채 남은 파일 등. | 격리 폴더로 옮긴다. 필요한 문서였다면 그 파일을 다시 업로드한다. |
| `leftover_pending` | 행이 없는 `.<id>.pdf.deleting`. 삭제는 COMMIT됐지만 사본 삭제가 실패했다. | 격리 폴더로 옮긴다. |
| `stale_pending` | 원본과 `.deleting`이 함께 있다. | 두 파일의 해시가 같으면 `.deleting`을 격리한다. 다르면 `content_hash`와 같은 쪽을 남기고 다른 쪽을 격리한다. |
| `hash_mismatch` | 원본 바이트가 `content_hash`와 다르다(손상·교체). | 원본을 격리하고 `missing_original`과 같이 백업에서 복사하거나 다시 업로드한다. |
| `page_count_mismatch` | 페이지 행 수가 `page_count`와 다르다. | 원본으로 다시 업로드한다(API 삭제 후 업로드). 백업 시점 이후 바뀐 문서가 아니면 복원으로도 된다. |
| `not_regular_file`, `unknown_file` | 문서 이름의 폴더·링크, 또는 형식 밖 파일. | 원인을 확인하고 격리 폴더로 옮긴다. |
| `busy` | 문서 잠금이 오래 잡혀 있다. | 다시 실행한다. 계속되면 멈춘 요청·세션을 `pg_stat_activity`에서 확인한다. |

## 4. 같은 시점 백업·복원

```sh
# 백업 (소유자 계정). BACKUP_DIR은 새 경로여야 한다.
DATABASE_URL="$OWNER_URL" UPLOAD_DIR=/srv/hsrule/uploads python -m hsrule_web.backup create /backup/hsrule-$(date +%Y%m%d-%H%M)
# 백업 검증 (DB 불필요)
python -m hsrule_web.backup verify /backup/hsrule-...
# 복원 (빈 DB·빈 업로드 폴더) → 앱 계정 권한 다시 부여 → 서버 시작
DATABASE_URL="$NEW_OWNER_URL" UPLOAD_DIR=/srv/hsrule/uploads python -m hsrule_web.backup restore /backup/hsrule-...
DATABASE_URL="$NEW_OWNER_URL" python -m hsrule_web.documents --grant-role hsrule_app
```

- **같은 시점 보장.** `create`는 `web_documents`에 `EXCLUSIVE` 잠금을 잡은 상태에서 행(`COPY`)과 원본 PDF를 함께 복사한다. 이 동안 조회·다운로드는 계속되고 업로드·삭제·본문 조회(`FOR SHARE`)는 잠시 기다린다. 이미 시작된 업로드·삭제는 끝난 뒤에 복사한다. 복사한 원본은 DB의 `content_hash`와 대조하고, 원본이 없거나 다르면 백업을 거부하고 부분 결과를 지운다. `manifest.json`(복구 시점, 행 수, 모든 파일의 SHA-256, migration 이력)은 마지막에 쓰므로 manifest가 없는 폴더는 미완성 백업이다.
- 백업에는 DB에 등록된 원본만 들어간다. 미등록 파일·`.deleting` 사본은 복구 시점의 일부가 아니므로 제외된다.
- **복원 안전장치.** `web_documents`가 비어 있지 않거나 업로드 폴더가 비어 있지 않으면 거부한다(덮어쓰지 않음). 모든 백업 파일의 해시와 migration 이력(현재 코드와 같아야 함)을 먼저 검증한다. 도중에 실패하면 migration까지 롤백되고 복사한 파일을 지우므로 다시 시도할 수 있다. 끝나면 `storage_check --verify-hash`를 자동 실행한다.
- 권한(GRANT)은 DB마다 따로이므로 새 DB에 복원한 뒤 `--grant-role`을 다시 실행한다.
- 이 도구는 웹 문서 테이블만 다룬다. DB 전체(`hs_reg_*` 포함)는 `pg_dump`로 따로 백업한다.

### 격리 리허설 기록 (2026-11-26)

로컬 Docker PostgreSQL 16.16에 원본(`hsrule_rehearsal_src`)과 교체 서버(`hsrule_rehearsal_dst`) DB, 전용 앱 계정을 새로 만들어 위 명령 그대로 실행했다. 코드는 `8b783df`. 끝난 뒤 두 DB·계정·파일을 모두 지웠다.

| 단계 | 실행 | 결과 |
|---|---|---|
| 1 | src·dst DB, 앱 계정 생성, `public` CREATE 회수 | 완료 |
| 2 | `documents --grant-role` 두 번 | 1회차 `0001_web_documents.sql` 적용·권한 부여, 2회차 `none (already current)` |
| 3 | 앱 계정으로 1~5페이지 PDF 5건 저장, `DROP TABLE` 시도 | 저장 5건, `ERROR: must be owner of table web_document_pages` |
| 4 | `storage_check --verify-hash` | `OK`, exit 0 |
| 5 | 별도 프로세스가 앱 계정으로 4초간 계속 업로드하는 중 `backup create` | 복구 시점 `02:43:20.875Z`에 문서 93·페이지 191·PDF 93. 부하 프로세스는 같은 4초 동안 347건 COMMIT(백업이 쓰기를 오래 막지 않음) |
| 6 | 백업 이후 src에서 문서 1건 삭제 | src 351건 |
| 7 | `backup verify` → dst에 `backup restore` → `--grant-role` | verify exit 0, 복원 문서 93·페이지 191·PDF 93, 자동 `storage_check --verify-hash: OK`, exit 0 |
| 8 | 앱 계정으로 dst 대조 | 행 93 = manifest, 6단계에서 지운 문서 복원됨(시점 재현), 모든 PDF가 DB 해시·manifest와 일치, 페이지 행 191 = manifest, 점검 exit 0 |
| 9 | 같은 dst에 다시 `restore` | `restore refused: 복원 대상 업로드 폴더가 비어 있지 않습니다`, exit 2 |

리허설 스크립트는 저장소에 넣지 않았다. 위 표의 명령은 4절과 같고, 부하 생성은 `DocumentStore.save`를 반복 호출했다.

## 미검증 범위

- 실제 운영 DB·운영 서버 업로드 폴더에는 적용하지 않았다. 운영 PostgreSQL 버전·관리형 DB의 역할 제약(예: 슈퍼유저 없음)·디스크는 확인하지 않았다.
- Windows는 열린 파일의 이름 변경을 막으므로 "다운로드 중 삭제" 동작이 다르다(해당 테스트는 Windows에서 건너뜀). 운영 서버는 Linux를 전제로 한다.
- 백업 잠금 시간은 원본 총용량에 비례한다. 리허설은 PDF 93건(소용량)이라 짧았다. 대용량에서는 한가한 시간에 실행하고 잠금 시간을 측정한다.
- 업로드 폴더가 NFS 등 네트워크 파일 시스템일 때의 `fsync`·이름 변경 원자성은 확인하지 않았다.
- `COPY` 텍스트 형식은 같은 메이저 버전 사이 복원만 리허설했다.

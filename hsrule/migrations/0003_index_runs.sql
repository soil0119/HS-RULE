-- 색인 실행 기록. 부분 실패와 성공 상태를 남겨 중단된 실행을 알아볼 수 있게 한다.
-- finished_at이 NULL인 행은 중단된 실행이다.
CREATE TABLE IF NOT EXISTS hs_reg_index_runs (
    id bigserial PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    embedding_model text NOT NULL,
    snapshot_revisions jsonb NOT NULL,
    report jsonb,
    ok boolean
);

CREATE INDEX IF NOT EXISTS hs_reg_index_runs_started ON hs_reg_index_runs (started_at DESC);

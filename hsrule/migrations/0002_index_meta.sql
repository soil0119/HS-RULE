-- 색인 전체가 한 임베딩 모델·차원·청킹 설정을 쓰도록 단일 행 메타를 둔다.
-- 서로 다른 모델이나 청킹으로 만든 벡터가 한 색인에 섞이면 거리 비교가 무의미해진다.
CREATE TABLE IF NOT EXISTS hs_reg_index_meta (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    embedding_model text NOT NULL,
    embedding_dim integer NOT NULL CHECK (embedding_dim > 0),
    chunk_params jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

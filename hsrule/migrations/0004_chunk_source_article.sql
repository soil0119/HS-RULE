-- 수집기 DOM 조문 ID(#60). 사이트 조문 ID는 개정이 바뀌어도 같은 조문을 가리킨다.
-- 청크 ID(개정:순번)는 개정마다 새로 매기므로, 같은 조문을 잇는 안정 키는 (regulation_id, source_article_id)다.
-- 조문 범위가 없는 입력(발췌본·이전 스냅샷)으로 만든 청크는 빈 문자열이다.
ALTER TABLE hs_reg_chunks ADD COLUMN IF NOT EXISTS source_article_id text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS hs_reg_chunks_source_article ON hs_reg_chunks (regulation_id, source_article_id)
    WHERE source_article_id <> '';

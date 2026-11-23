"""공식 규정 배치 색인과 개정·삭제 재색인 수명주기 (#25)."""
import json
import math
import sys
import time

from hsrule.official_chunking import ChunkingError, chunk_regulation, verify_chunks
from hsrule.official_repository import EMBEDDING_DIM

# 색인 전 임베딩 모델을 확인할 한국어 표본. 가까운 문장 쌍과 관련 없는 문장을 비교한다.
KOREAN_PROBES = ("조기졸업은 7학기에 할 수 있다.", "조기졸업은 일곱 학기 만에 가능하다.",
                 "교내 주차 요금은 시간당 1,000원이다.")


class IndexingError(RuntimeError):
    """색인에 실패했다. 이전에 저장된 색인은 그대로 유지된다."""


def _cosine(left, right) -> float:
    norm = math.sqrt(sum(v * v for v in left)) * math.sqrt(sum(v * v for v in right))
    return sum(a * b for a, b in zip(left, right)) / norm if norm else 0.0


def validate_embeddings(embeddings, max_tokens: int = 128) -> dict:
    """색인 전에 설정된 임베딩 모델의 길이·차원·한국어 처리를 확인한다.

    길이: 토크나이저가 알리는 최대 입력 길이가 청크 토큰 한도보다 작으면 뒤쪽이 잘린다.
    차원: 스키마가 고정한 384차원과 다르면 저장 자체가 불가능하다.
    한국어: 미지 토큰 비율이 높거나 뜻이 가까운 문장과 관련 없는 문장을 구분하지 못하면 거부한다.
    """
    tokenizer = embeddings.tokenizer
    limit = getattr(tokenizer, "model_max_length", None)
    if isinstance(limit, int) and 0 < limit < 1_000_000 and max_tokens > limit:
        raise IndexingError(f"임베딩 모델의 최대 입력은 {limit} 토큰인데 청크 한도가 {max_tokens}입니다.")
    encoded = tokenizer(KOREAN_PROBES[0], add_special_tokens=False)["input_ids"]
    unknown = getattr(tokenizer, "unk_token_id", None)
    if not encoded:
        raise IndexingError("임베딩 토크나이저가 한국어 문장에서 토큰을 만들지 못했습니다.")
    if unknown is not None and sum(1 for token in encoded if token == unknown) > len(encoded) / 2:
        raise IndexingError("임베딩 토크나이저가 한국어를 대부분 미지 토큰으로 처리합니다.")
    vectors = embeddings.embed_documents(list(KOREAN_PROBES))
    if len(vectors) != len(KOREAN_PROBES):
        raise IndexingError("임베딩 개수가 입력 문장 수와 다릅니다.")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise IndexingError(f"임베딩 차원이 {len(vector)}입니다. 스키마는 {EMBEDDING_DIM}차원을 요구합니다.")
        if not all(math.isfinite(value) for value in vector) or not any(vector):
            raise IndexingError("임베딩에 유한하지 않은 값이 있거나 영벡터입니다.")
    near, far = _cosine(vectors[0], vectors[1]), _cosine(vectors[0], vectors[2])
    if near <= far:
        raise IndexingError(f"모델이 한국어 의미를 구분하지 못합니다(유사 {near:.3f} <= 무관 {far:.3f}).")
    return {"model": embeddings.model_name, "max_input_tokens": limit,
            "korean_similar": round(near, 4), "korean_unrelated": round(far, 4)}


def _embed_in_batches(embeddings, texts: list[str], batch_size: int, *, attempts: int = 3,
                      backoff: float = 0.5, sleep=time.sleep) -> list[list[float]]:
    """배치로 임베딩한다. 실패한 배치만 다시 시도하고, 끝까지 실패하면 그대로 올린다."""
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        for attempt in range(1, attempts + 1):
            try:
                result = embeddings.embed_documents(batch)
            except Exception:
                if attempt == attempts:
                    raise
                sleep(backoff * attempt)
                continue
            if len(result) != len(batch):
                raise IndexingError("임베딩 개수가 입력 청크 수와 다릅니다.")
            vectors.extend(result)
            break
    return vectors


def index_regulation(regulation: dict, repository, embeddings, *, max_tokens: int = 128,
                     overlap_tokens: int = 16, batch_size: int = 32, make_current: bool = True,
                     force: bool = False, attempts: int = 3, sleep=time.sleep) -> dict:
    """검증된 공식 규정 한 건을 색인한다.

    본문·모델·청킹 설정·공개 범위가 모두 같으면 건너뛴다(unchanged). 본문은 같고 공개 범위만
    바뀌었으면 다시 임베딩하지 않고 범위만 반영한다(rescoped).
    폐지된 규정(abolished)은 색인에서 제거한다(abolished).
    """
    params = {"max_tokens": max_tokens, "overlap_tokens": overlap_tokens}
    if regulation.get("abolished"):
        removed = repository.delete_regulation(regulation["regulation_id"])
        return {"status": "abolished", "revision_id": regulation["revision_id"], "chunks": 0,
                "removed_revisions": removed}
    visibility = regulation.get("visibility", "public")
    try:
        chunks = chunk_regulation(regulation, embeddings.tokenizer, max_tokens, overlap_tokens)
    except ChunkingError as exc:
        raise IndexingError(str(exc)) from exc
    problems = verify_chunks(regulation, chunks, embeddings.tokenizer, max_tokens)
    if problems:
        raise IndexingError("청킹 검증 실패: " + "; ".join(problems[:3]))
    text_hash = chunks[0]["text_hash"]
    existing = repository.get_revision(regulation["revision_id"])
    same_content = (existing and not force and existing["text_hash"] == text_hash
                    and existing["embedding_model"] == embeddings.model_name
                    and existing["chunk_params"] == params and existing["is_current"] == make_current)
    if same_content and existing["visibility"] == visibility:
        return {"status": "unchanged", "revision_id": regulation["revision_id"], "chunks": existing["chunk_count"]}
    if same_content:  # 공개 범위만 달라졌다.
        repository.set_visibility(regulation["regulation_id"], visibility)
        return {"status": "rescoped", "revision_id": regulation["revision_id"], "chunks": existing["chunk_count"]}
    vectors = _embed_in_batches(embeddings, [chunk["embedding_text"] for chunk in chunks], batch_size,
                                attempts=attempts, sleep=sleep)
    revision = {"revision_id": regulation["revision_id"], "regulation_id": regulation["regulation_id"],
                "title": regulation["title"], "official_url": regulation["official_url"],
                "text_hash": text_hash, "visibility": visibility,
                "embedding_model": embeddings.model_name, "chunk_params": params}
    repository.save_revision(revision, chunks, vectors, make_current=make_current)
    return {"status": "indexed", "revision_id": regulation["revision_id"], "chunks": len(chunks)}


def sync_snapshot(regulations: list[dict], repository, embeddings, *, keep_previous: int = 1,
                  preflight: bool = True, record_run: bool = True, **options) -> dict:
    """검증된 전체 스냅샷과 색인을 맞춘다.

    - 스냅샷의 각 규정을 색인한다. 한 규정이 실패해도 나머지는 계속하고 실패를 보고한다.
    - 스냅샷에 없는 규정은 색인에서 지운다. 단, 스냅샷이 비었거나 실패가 있으면 아무것도 지우지 않는다.
    - 규정마다 현재 개정과 이전 keep_previous개 개정만 보관한다.
    - 실행 기록을 DB에 남긴다. 중단되면 finished_at이 비어 있어 재개가 필요함을 알 수 있다.
    """
    report = {"indexed": [], "unchanged": [], "rescoped": [], "abolished": [], "failed": [], "removed": [],
              "pruned": 0, "preflight": None}
    if preflight and regulations:
        report["preflight"] = validate_embeddings(embeddings, options.get("max_tokens", 128))
    run_id = repository.start_run(embeddings.model_name,
                                 [r.get("revision_id") for r in regulations]) if record_run else None
    try:
        seen = set()
        for regulation in regulations:
            seen.add(regulation.get("regulation_id"))
            try:
                result = index_regulation(regulation, repository, embeddings, **options)
            except Exception as exc:  # 한 규정의 실패가 전체 스냅샷을 막지 않는다.
                report["failed"].append({"regulation_id": regulation.get("regulation_id"),
                                         "revision_id": regulation.get("revision_id"), "error": str(exc)})
                continue
            report[result["status"]].append(result["revision_id"])
            if result["status"] != "abolished":
                report["pruned"] += repository.prune_revisions(regulation["regulation_id"], keep_previous)
        if regulations and not report["failed"]:
            for regulation_id in repository.regulation_ids():
                if regulation_id not in seen:
                    repository.delete_regulation(regulation_id)
                    report["removed"].append(regulation_id)
    finally:
        if run_id is not None:
            repository.finish_run(run_id, report, not report["failed"])
    return report


def main(argv: list[str]) -> int:
    import os

    from hsrule.embeddings import LocalEmbeddings
    from hsrule.official_repository import OfficialRepository

    if len(argv) != 2 or not os.environ.get("DATABASE_URL"):
        print("사용법: DATABASE_URL=... python -m hsrule.official_indexer <snapshot.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        regulations = json.load(handle)["regulations"]
    repository = OfficialRepository(os.environ["DATABASE_URL"])
    repository.initialize()
    report = sync_snapshot(regulations, repository, LocalEmbeddings())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

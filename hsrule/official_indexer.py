"""공식 규정 배치 색인과 개정·삭제 재색인 수명주기 (#25)."""
import json
import sys

from hsrule.official_chunking import ChunkingError, chunk_regulation, verify_chunks


class IndexingError(RuntimeError):
    """색인에 실패했다. 이전에 저장된 색인은 그대로 유지된다."""


def _embed_in_batches(embeddings, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        result = embeddings.embed_documents(batch)
        if len(result) != len(batch):
            raise IndexingError("임베딩 개수가 입력 청크 수와 다릅니다.")
        vectors.extend(result)
    return vectors


def index_regulation(regulation: dict, repository, embeddings, *, max_tokens: int = 128,
                     overlap_tokens: int = 16, batch_size: int = 32, make_current: bool = True,
                     force: bool = False) -> dict:
    """검증된 공식 규정 한 건을 색인한다. 본문·모델·청킹 설정이 같으면 건너뛴다."""
    params = {"max_tokens": max_tokens, "overlap_tokens": overlap_tokens}
    try:
        chunks = chunk_regulation(regulation, embeddings.tokenizer, max_tokens, overlap_tokens)
    except ChunkingError as exc:
        raise IndexingError(str(exc)) from exc
    problems = verify_chunks(regulation, chunks, embeddings.tokenizer, max_tokens)
    if problems:
        raise IndexingError("청킹 검증 실패: " + "; ".join(problems[:3]))
    text_hash = chunks[0]["text_hash"]
    existing = repository.get_revision(regulation["revision_id"])
    if (existing and not force and existing["text_hash"] == text_hash
            and existing["embedding_model"] == embeddings.model_name and existing["chunk_params"] == params
            and existing["is_current"] == make_current):
        return {"status": "unchanged", "revision_id": regulation["revision_id"], "chunks": existing["chunk_count"]}
    vectors = _embed_in_batches(embeddings, [chunk["embedding_text"] for chunk in chunks], batch_size)
    revision = {"revision_id": regulation["revision_id"], "regulation_id": regulation["regulation_id"],
                "title": regulation["title"], "official_url": regulation["official_url"],
                "text_hash": text_hash, "visibility": regulation.get("visibility", "public"),
                "embedding_model": embeddings.model_name, "chunk_params": params}
    repository.save_revision(revision, chunks, vectors, make_current=make_current)
    return {"status": "indexed", "revision_id": regulation["revision_id"], "chunks": len(chunks)}


def sync_snapshot(regulations: list[dict], repository, embeddings, *, keep_previous: int = 1,
                  **options) -> dict:
    """검증된 전체 스냅샷과 색인을 맞춘다.

    - 스냅샷의 각 규정을 색인한다. 한 규정이 실패해도 나머지는 계속하고 실패를 보고한다.
    - 스냅샷에 없는 규정은 색인에서 지운다. 단, 스냅샷이 비었거나 실패가 있으면 아무것도 지우지 않는다.
    - 규정마다 현재 개정과 이전 keep_previous개 개정만 보관한다.
    """
    report = {"indexed": [], "unchanged": [], "failed": [], "removed": [], "pruned": 0}
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
        report["pruned"] += repository.prune_revisions(regulation["regulation_id"], keep_previous)
    if regulations and not report["failed"]:
        for regulation_id in repository.regulation_ids():
            if regulation_id not in seen:
                repository.delete_regulation(regulation_id)
                report["removed"].append(regulation_id)
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

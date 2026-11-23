"""공식 규정 검색·인용 평가 (#11).

사용: DATABASE_URL=<일회용 PostgreSQL> python scripts/run_rag_eval.py --mode standin|real [--snapshot snapshot.json]

- standin: 결정적 대역 임베딩(문자 2-gram 해시)으로 파이프라인(조항 번호·키워드·결합 순위·필터)만 점검한다.
  모델 품질에 대한 주장이 아니며 답변 지표는 측정하지 않는다.
- real: 실제 임베딩(LocalEmbeddings)과 Groq 모델(GROQ_API_KEY, GROQ_MODEL)로 답변·인용 지표까지 측정한다.
측정하지 못한 값은 결과 JSON의 not_measured에 사유와 함께 적고, 수치를 임의로 채우지 않는다.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hsrule.official_indexer import index_regulation  # noqa: E402
from hsrule.official_repository import OfficialRepository  # noqa: E402
from hsrule.official_retrieval import HybridRetriever, query_terms  # noqa: E402

KS = (1, 3, 5)
DEFAULT_REGULATION = {"regulation_id": "20", "revision_id": "20:1623", "title": "한성대학교 학칙",
                      "official_url": "https://rule.hansung.ac.kr/lmxsrv/law/lawFullContent.do?SEQ=20&SEQ_HISTORY=1623"}


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def first_rank(article_ids, expected):
    for rank, article in enumerate(article_ids, 1):
        if article in expected:
            return rank
    return None


def retrieval_metrics(rows):
    """rows: [{rank: int|None}] → Recall@k, MRR. 대상이 없으면 None."""
    if not rows:
        return {"cases": 0, "recall": None, "mrr": None}
    return {"cases": len(rows),
            "recall": {f"@{k}": sum(1 for r in rows if r["rank"] and r["rank"] <= k) / len(rows) for k in KS},
            "mrr": sum(1 / r["rank"] for r in rows if r["rank"]) / len(rows)}


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def load_regulations(args):
    if args.snapshot:
        return json.loads(Path(args.snapshot).read_text(encoding="utf-8"))["regulations"]
    text = Path(args.corpus).read_text(encoding="utf-8")
    return [dict(DEFAULT_REGULATION, text=text)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standin", "real"), required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--corpus", default=str(ROOT / "tests/fixtures/hansung_rules_excerpt.txt"))
    parser.add_argument("--eval", default=str(ROOT / "eval/regulation_eval.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "eval/results"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL(일회용 PostgreSQL)이 필요합니다. 평가는 색인을 비우고 다시 만듭니다.")

    regulations = load_regulations(args)
    cases = [json.loads(line) for line in Path(args.eval).read_text(encoding="utf-8").splitlines() if line.strip()]
    corpus_text = "".join(r["text"] for r in regulations)
    if args.mode == "standin":
        from tests.fakes import FakeEmbeddings
        embeddings, llm, settings = FakeEmbeddings(corpus_text), None, None
    else:
        from hsrule.embeddings import LocalEmbeddings
        from hsrule.rag_config import RagSettings, create_llm
        settings = RagSettings.from_env()
        embeddings, llm = LocalEmbeddings(), create_llm(settings)

    repository = OfficialRepository(url)
    repository.initialize()
    with repository.cursor() as cursor:
        cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
    for regulation in regulations:
        index_regulation(regulation, repository, embeddings)
    retriever = HybridRetriever(repository, embeddings, k=max(KS))

    not_measured = {}
    rows = {"hybrid": [], "keyword_only": [], "vector_only": []}
    latencies, absent_failures, skipped = [], [], []
    for case in cases:
        expected = case.get("expected_articles")
        if case.get("history") and args.mode == "standin":
            skipped.append({"id": case["id"], "reason": "후속 질문은 질문 재작성에 LLM이 필요함"})
            continue
        started = time.perf_counter()
        hits = retriever.search(case["question"], k=max(KS))
        latencies.append(time.perf_counter() - started)
        ids = [h["article_id"] for h in hits]
        if case.get("expect_absent_articles") and set(ids) & set(case["expect_absent_articles"]):
            absent_failures.append(case["id"])
        if expected:
            rows["hybrid"].append({"id": case["id"], "type": case["type"], "rank": first_rank(ids, expected)})
            keyword = repository.keyword_candidates(query_terms(case["question"]), limit=max(KS))
            vector = repository.vector_candidates(embeddings.embed_query(case["question"]), limit=max(KS))
            rows["keyword_only"].append({"rank": first_rank([h["article_id"] for h in keyword], expected)})
            rows["vector_only"].append({"rank": first_rank([h["article_id"] for h in vector], expected)})

    answers = None
    if llm is None:
        not_measured["answer_quality"] = "실제 Groq 모델이 없는 standin 모드: 인용 일치·무근거 답변·거절은 측정하지 않음"
    else:
        from hsrule.answering import CitedAnswerer, RagError
        answerer = CitedAnswerer(llm, retriever, k=settings.retrieval_k)
        answers, answer_latency = [], []
        for case in cases:
            started = time.perf_counter()
            try:
                result = answerer.answer(case["question"], case.get("history"), language=case["language"])
                error = None
            except RagError as exc:
                result, error = None, exc.code
            answer_latency.append(time.perf_counter() - started)
            cited = [s["article_id"] for s in (result or {}).get("sources", [])]
            expected = case.get("expected_articles")
            answers.append({"id": case["id"], "type": case["type"], "error": error,
                            "found": None if result is None else result["found"], "cited": cited,
                            "citation_match": bool(expected) and bool(cited) and set(cited) <= set(expected),
                            "expected_found": case.get("expected_found", bool(expected)),
                            "revision_match": all(s["revision_id"] == regulations[0]["revision_id"]
                                                  for s in (result or {}).get("sources", []))})
        latencies_answer = {"p50": percentile(answer_latency, .5), "p95": percentile(answer_latency, .95)}
        answerable = [a for a in answers if a["expected_found"]]
        unanswerable = [a for a in answers if not a["expected_found"]]
        summary = {
            "cases": len(answers), "errors": sum(1 for a in answers if a["error"]),
            "citation_match_rate": (sum(a["citation_match"] for a in answerable) / len(answerable)) if answerable else None,
            "revision_match_rate": (sum(a["revision_match"] for a in answers if a["found"]) /
                                    max(1, sum(1 for a in answers if a["found"]))),
            "unsupported_answer_rate": (sum(1 for a in unanswerable if a["found"]) / len(unanswerable)) if unanswerable else None,
            "correct_refusal_rate": (sum(1 for a in unanswerable if a["found"] is False) / len(unanswerable)) if unanswerable else None,
            "latency_seconds": latencies_answer}
        answers = {"summary": summary, "cases": answers}

    result = {
        "mode": args.mode,
        "claim": ("대역 임베딩으로 파이프라인만 점검한 결과이며 모델 품질을 뜻하지 않는다." if args.mode == "standin"
                  else "실제 임베딩·Groq 모델로 측정한 결과."),
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "git_commit": git_commit(), "python": platform.python_version(),
            "corpus_sha256": hashlib.sha256(corpus_text.encode("utf-8")).hexdigest(),
            "corpus_revisions": [r["revision_id"] for r in regulations],
            "eval_set_sha256": hashlib.sha256(Path(args.eval).read_bytes()).hexdigest(), "eval_cases": len(cases),
            "embedding_model": embeddings.model_name, "chunk_params": {"max_tokens": 128, "overlap_tokens": 16},
            "tokenizer": type(embeddings.tokenizer).__name__ if args.mode == "standin" else embeddings.model_name,
            "groq_model": settings.groq_model if settings else None,
            "prompt_sha256": hashlib.sha256(Path(ROOT / "hsrule/chains.py").read_bytes() +
                                            Path(ROOT / "hsrule/answering.py").read_bytes()).hexdigest()},
        "retrieval": {"hybrid": retrieval_metrics(rows["hybrid"]), "keyword_only": retrieval_metrics(rows["keyword_only"]),
                      "vector_only": retrieval_metrics(rows["vector_only"]),
                      "per_case": rows["hybrid"], "skipped": skipped,
                      "deleted_article_regressions": absent_failures,
                      "search_latency_seconds": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95)}},
        "answers": answers, "not_measured": not_measured,
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    path = Path(args.out) / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{args.mode}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mode", "claim")}, ensure_ascii=False))
    print(json.dumps(result["retrieval"]["hybrid"], ensure_ascii=False))
    print("저장:", path)


if __name__ == "__main__":
    main()

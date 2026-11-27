"""공식 규정 검색·인용 평가 (#11).

사용: DATABASE_URL=<일회용 PostgreSQL> python scripts/run_rag_eval.py --mode standin|real [--snapshot snapshot.json]

- standin: 결정적 대역 임베딩(문자 2-gram 해시)으로 파이프라인(조항 번호·키워드·결합 순위·필터)만 점검한다.
  모델 품질에 대한 주장이 아니며 답변 지표는 측정하지 않는다.
- real: 실제 임베딩(LocalEmbeddings)과 LLM(기본 Gemini: GEMINI_API_KEY, GEMINI_MODEL / LLM_PROVIDER=groq면 GROQ_*)로 답변·인용 지표까지 측정한다.
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

from hsrule.official_indexer import IndexingError, index_regulation  # noqa: E402
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


def run_regressions(repository, embeddings, retriever, regulations) -> dict:
    """공개/비공개·삭제·개정·색인 실패·잘못된 인용 회귀. 모델 없이 결정적으로 확인한다."""
    checks = {}
    private = {"regulation_id": "eval-private", "revision_id": "eval-private:1", "title": "평가용 비공개 규정",
               "official_url": "https://rule.example/eval-private", "visibility": "private",
               "text": "제 1 조 (비공개 기준)\n평가용 비공개 본문이며 공개 질의에 노출되면 안 된다.\n"}
    index_regulation(private, repository, embeddings)
    try:
        public_hits = retriever.search("평가용 비공개 본문 노출", include_private=False)
        admin_hits = retriever.search("평가용 비공개 본문 노출", include_private=True)
        checks["private_not_leaked"] = all(h["regulation_id"] != "eval-private" for h in public_hits)
        checks["admin_sees_private"] = any(h["regulation_id"] == "eval-private" for h in admin_hits)

        first = regulations[0]
        revised = dict(first, revision_id=first["revision_id"] + "-eval",
                       text=first["text"] + "\n제 900 조 (평가용 신설)\n평가용으로 더한 조항이다.\n")
        index_regulation(revised, repository, embeddings)
        new_hits = retriever.search("제900조 평가용 신설")
        checks["new_revision_is_served"] = any(h["revision_id"] == revised["revision_id"] for h in new_hits)
        checks["old_revision_not_served"] = all(h["revision_id"] != first["revision_id"] for h in new_hits)
        checks["deleted_article_excluded"] = all(
            h["article_id"] != "제17조" for h in retriever.search("제17조 입학생 등록 및 구비서류"))

        try:
            index_regulation(dict(first, revision_id="eval-broken:1", official_url=""), repository, embeddings)
            checks["index_failure_rejected"] = False
        except IndexingError:
            checks["index_failure_rejected"] = repository.get_revision("eval-broken:1") is None

        checks["bad_citation_rejected"] = bad_citation_is_rejected(retriever)
    finally:
        repository.delete_regulation("eval-private")
        for regulation in regulations:
            repository.delete_regulation(regulation["regulation_id"])
            index_regulation(regulation, repository, embeddings)
    checks["all_passed"] = all(checks.values())
    return checks


def bad_citation_is_rejected(retriever) -> bool:
    """후보에 없는 라벨을 인용하면 거부되는지 확인한다(대역 모델로 계약만 확인)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from hsrule.answering import CitedAnswerer, RagError

    payload = '{"found": true, "answer": "답", "citations": ["S99"], "followups": []}'
    model = GenericFakeChatModel(messages=iter([AIMessage(payload)]))
    try:
        CitedAnswerer(model, retriever).answer("제40조 조기졸업 조건")
    except RagError as error:
        return error.code == "rag_invalid_response"
    return False


def conflict_evaluation(path: Path, retriever, llm) -> dict:
    """충돌 분석 평가 (#29의 품질 평가를 여기 남긴다).

    모델이 없으면 각 문단이 정답 조항을 후보로 끌어오는지(후보 재현율)만 측정한다.
    모델이 있으면 실제 분석을 돌려 지적한 조항과 정답을 비교한다.
    """
    if not path.is_file():
        return {"cases": 0}
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for case in cases:  # 긴 문서 사례는 같은 문단을 반복해 분량을 만든다.
        if case.get("repeat_filler"):
            filler = "\n\n".join(f"제{index}항 일반 안내 문단이며 규정과 충돌하지 않는다." for index in range(case["repeat_filler"]))
            case["document"] = filler + "\n\n본 지침은 5학기만에 조기졸업을 허용한다."
    from hsrule.conflict import ConflictAnalyzer, split_passages

    results = []
    analyzer = ConflictAnalyzer(llm, retriever) if llm is not None else None
    for case in cases:
        expected = set(case["expected_articles"])
        candidate_articles = set()
        for passage in split_passages(case["document"]):
            candidate_articles.update(h["article_id"] for h in retriever.search(passage[:500], include_private=True,
                                                                               k=3))
        entry = {"id": case["id"], "type": case["type"], "expected": sorted(expected),
                 "candidate_recall": (len(expected & candidate_articles) / len(expected)) if expected else None}
        if analyzer is not None:
            started = time.perf_counter()
            analysis = analyzer.analyze(case["document"], case["filename"], include_private=True,
                                        language=case["language"])
            flagged = {item["article"] for item in analysis["conflicts"]}
            entry.update({"flagged": sorted(flagged), "expected_found": sorted(expected & flagged),
                          "false_positives": sorted(flagged - expected) if expected or case["type"] != "conflict" else [],
                          "recommendations_with_evidence": all(item.get("evidence") for item in analysis["recommendations"]),
                          "scope": analysis["scope"], "seconds": round(time.perf_counter() - started, 3)})
        results.append(entry)
    measured = [r["candidate_recall"] for r in results if r["candidate_recall"] is not None]
    return {"cases": len(results), "candidate_recall_mean": (sum(measured) / len(measured)) if measured else None,
            "per_case": results}


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
    parser.add_argument("--conflict-eval", default=str(ROOT / "eval/conflict_cases.jsonl"))
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
        if args.mode == "standin" and case.get("history"):
            skipped.append({"id": case["id"], "reason": "후속 질문은 질문 재작성에 LLM이 필요함"})
            continue
        if args.mode == "standin" and case["language"] != "ko":
            skipped.append({"id": case["id"], "reason": "영어 질의는 다국어 임베딩이 필요하며 대역 임베딩으로는 측정 불가"})
            continue
        started = time.perf_counter()
        hits = retriever.search(case["question"], k=max(KS))
        latencies.append(time.perf_counter() - started)
        ids = [h["article_id"] for h in hits]
        if case.get("expect_absent_articles") and set(ids) & set(case["expect_absent_articles"]):
            absent_failures.append(case["id"])
        if expected:
            wrong_url = [h["official_url"] for h in hits
                         if h["article_id"] in expected and not h["official_url"].startswith("http")]
            rows["hybrid"].append({"id": case["id"], "type": case["type"], "rank": first_rank(ids, expected),
                                   "revision": next((h["revision_id"] for h in hits
                                                     if h["article_id"] in expected), None),
                                   "url_ok": not wrong_url})
            keyword = repository.keyword_candidates(query_terms(case["question"]), limit=max(KS))
            vector = repository.vector_candidates(embeddings.embed_query(case["question"]), limit=max(KS))
            rows["keyword_only"].append({"rank": first_rank([h["article_id"] for h in keyword], expected)})
            rows["vector_only"].append({"rank": first_rank([h["article_id"] for h in vector], expected)})

    regressions = run_regressions(repository, embeddings, retriever, regulations)
    conflicts = conflict_evaluation(Path(args.conflict_eval), retriever, llm)

    answers = None
    if llm is None:
        not_measured["answer_quality"] = "실제 LLM이 없는 standin 모드: 인용 일치·무근거 답변·거절은 측정하지 않음"
        not_measured["conflict_quality"] = "실제 LLM이 없는 standin 모드: 충돌 지적 정확도는 측정하지 않고 후보 재현율만 기록"
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
            sources = (result or {}).get("sources", [])
            cited = [s["article_id"] for s in sources]
            expected = case.get("expected_articles")
            known_revisions = {r["revision_id"] for r in regulations}
            answers.append({"id": case["id"], "type": case["type"], "error": error,
                            "found": None if result is None else result["found"], "cited": cited,
                            "cited_chunks": [s["chunk_id"] for s in sources],
                            "citation_match": bool(expected) and bool(cited) and set(cited) <= set(expected),
                            "expected_found": case.get("expected_found", bool(expected)),
                            "url_ok": all(str(s.get("url", "")).startswith("http") for s in sources),
                            "revision_match": all(s["revision_id"] in known_revisions for s in sources)})
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
                  else "실제 임베딩·LLM으로 측정한 결과."),
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "git_commit": git_commit(), "python": platform.python_version(),
            "corpus_sha256": hashlib.sha256(corpus_text.encode("utf-8")).hexdigest(),
            "corpus_revisions": [r["revision_id"] for r in regulations],
            "eval_set_sha256": hashlib.sha256(Path(args.eval).read_bytes()).hexdigest(), "eval_cases": len(cases),
            "embedding_model": embeddings.model_name, "chunk_params": {"max_tokens": 128, "overlap_tokens": 16},
            "tokenizer": type(embeddings.tokenizer).__name__ if args.mode == "standin" else embeddings.model_name,
            "llm_provider": settings.provider if settings else None,
            "llm_model": settings.model if settings else None,
            "prompt_sha256": hashlib.sha256(Path(ROOT / "hsrule/chains.py").read_bytes() +
                                            Path(ROOT / "hsrule/answering.py").read_bytes()).hexdigest()},
        "retrieval": {"hybrid": retrieval_metrics(rows["hybrid"]), "keyword_only": retrieval_metrics(rows["keyword_only"]),
                      "vector_only": retrieval_metrics(rows["vector_only"]),
                      "per_case": rows["hybrid"], "skipped": skipped,
                      "deleted_article_regressions": absent_failures,
                      "search_latency_seconds": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95)}},
        "answers": answers, "conflicts": conflicts, "regressions": regressions,
        "retrieval_limits": retriever.limits, "not_measured": not_measured,
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    path = Path(args.out) / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{args.mode}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mode", "claim")}, ensure_ascii=False))
    print(json.dumps(result["retrieval"]["hybrid"], ensure_ascii=False))
    print(json.dumps({"regressions": result["regressions"]["all_passed"],
                      "conflict_candidate_recall": result["conflicts"]["candidate_recall_mean"]},
                     ensure_ascii=False))
    print("저장:", path)


if __name__ == "__main__":
    main()

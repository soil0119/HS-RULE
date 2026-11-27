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


def case_rank(article_ids, expected, required=None):
    """검색 순위. required가 있으면 필요한 조항이 모두 나온 순위(가장 늦은 것), 하나라도 없으면 None(#77).

    예: 2014학번 졸업학점 질문은 본칙 제34조만 찾으면 실패이고 부칙 경과조치까지 찾아야 성공이다.
    """
    if not required:
        return first_rank(article_ids, expected)
    ranks = [first_rank(article_ids, {article}) for article in required]
    return None if None in ranks else max(ranks)


def citation_matches(cited, expected, required=None) -> bool:
    """인용 성공 판정(#59). 인용한 조항이 모두 허용 목록(expected) 안에 있고 하나 이상이어야 한다.

    required가 있으면 그 조항을 모두 인용해야 한다. 여러 조항을 허용하는 사례는 expected에 대체 가능한 근거를,
    required에 반드시 필요한 근거를 따로 적는다. 정답과 무관한 조항만 인용하면 실패다.
    """
    if not expected or not cited:
        return False
    if not set(cited) <= set(expected):
        return False
    return set(required or ()) <= set(cited)


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
            try:
                analysis = analyzer.analyze(case["document"], case["filename"], include_private=True,
                                            language=case["language"])
            except Exception as exc:  # 한 사례의 timeout·잘못된 JSON·예상 밖 오류가 다른 사례를 없애지 않는다(#58).
                entry.update({"error": _case_error(exc), "seconds": round(time.perf_counter() - started, 3)})
            else:
                flagged = {item["article"] for item in analysis["conflicts"]}
                entry.update({"flagged": sorted(flagged), "expected_found": sorted(expected & flagged),
                              "false_positives": sorted(flagged - expected) if expected or case["type"] != "conflict" else [],
                              "recommendations_with_evidence": all(item.get("evidence") for item in analysis["recommendations"]),
                              "scope": analysis["scope"], "seconds": round(time.perf_counter() - started, 3)})
        results.append(entry)
    measured = [r["candidate_recall"] for r in results if r["candidate_recall"] is not None]
    errors = sum(1 for r in results if r.get("error"))
    return {"cases": len(results), "analyzed": (len(results) - errors) if analyzer is not None else 0,
            "errors": errors, "candidate_recall_mean": (sum(measured) / len(measured)) if measured else None,
            "per_case": results}


def _case_error(exc) -> dict:
    """사례 오류 기록. provider 예외 본문은 원문·키가 섞일 수 있어 남기지 않고 계약 코드·예외 이름만 남긴다."""
    from hsrule.answering import RagError

    if isinstance(exc, RagError):
        return {"code": exc.code, "reason": exc.reason}
    return {"code": "unexpected_error", "reason": type(exc).__name__}


def evaluate_answers(cases, answerer, regulations, *, pause: float = 0.0, sleep=time.sleep) -> dict:
    """답변·인용 평가. 한 사례의 오류·지연을 기록하고 다음 사례를 계속 평가한다(#58).

    pause: 사례 사이 대기(초). 무료 API의 분당 토큰 한도(예: Groq 8K TPM) 때문에 사례가 한도 초과로
    오류 처리되면 품질이 아니라 한도를 재게 되므로, 실제 모델 평가에서는 사례 사이를 띄운다.
    """
    known_revisions = {r["revision_id"] for r in regulations}
    rows = []
    for index, case in enumerate(cases):
        if index and pause > 0:
            sleep(pause)
        started = time.perf_counter()
        try:
            result = answerer.answer(case["question"], case.get("history"), language=case["language"])
            error = None
        except Exception as exc:  # RagError 외의 예상 밖 오류도 사례 오류로 남기고 다음 사례를 계속한다.
            result, error = None, _case_error(exc)
        seconds = time.perf_counter() - started
        sources = (result or {}).get("sources", [])
        cited = [s["article_id"] for s in sources]
        expected = case.get("expected_articles")
        clarification = (result or {}).get("clarification") or {}
        grounding = (result or {}).get("grounding") or {}
        usage = (result or {}).get("usage") or {}
        rows.append({"id": case["id"], "type": case["type"], "split": case.get("split"),
                     "error": error, "seconds": round(seconds, 3),
                     "usage": {key: usage.get(key) for key in ("calls", "retries", "input_tokens", "output_tokens")}
                     if usage else None,
                     "clarification_asked": clarification.get("needed") if result is not None else None,
                     "expected_clarification": case.get("expected_clarification"),
                     "grounding_verified": grounding.get("verified") if result is not None else None,
                     "grounding_repaired": grounding.get("repaired") if result is not None else None,
                     "found": None if result is None else result["found"], "cited": cited,
                     "cited_chunks": [s["chunk_id"] for s in sources],
                     "citation_match": citation_matches(cited, expected, case.get("required_articles")),
                     "expected_found": case.get("expected_found", bool(expected)),
                     "url_ok": all(str(s.get("url", "")).startswith("http") for s in sources),
                     "revision_match": all(s["revision_id"] in known_revisions for s in sources)})
    return {"summary": summarize_answers(rows), "by_split": split_summaries(rows, summarize_answers), "cases": rows}


def summarize_answers(rows) -> dict:
    """집계 분모를 명시한다. 오류 사례는 성공으로도 실패 답변으로도 세지 않고 errors로 따로 센다."""
    completed = [r for r in rows if not r["error"]]
    answerable = [r for r in rows if r["expected_found"]]
    unanswerable = [r for r in rows if not r["expected_found"]]
    answerable_done = [r for r in answerable if not r["error"]]
    unanswerable_done = [r for r in unanswerable if not r["error"]]
    found = [r for r in completed if r["found"]]
    seconds = [r["seconds"] for r in rows]
    return {
        "cases": len(rows), "completed": len(completed), "errors": len(rows) - len(completed),
        "error_reasons": sorted({f"{r['error']['code']}/{r['error']['reason']}" for r in rows if r["error"]}),
        "denominators": {"answerable": len(answerable), "answerable_completed": len(answerable_done),
                         "unanswerable": len(unanswerable), "unanswerable_completed": len(unanswerable_done)},
        # 성공률은 오류 사례를 실패로 센다(분모는 전체). 무근거 답변률은 완료된 사례만 본다.
        "citation_match_rate": (sum(r["citation_match"] for r in answerable) / len(answerable)) if answerable else None,
        "revision_match_rate": (sum(r["revision_match"] for r in found) / len(found)) if found else None,
        "unsupported_answer_rate": (sum(1 for r in unanswerable_done if r["found"]) / len(unanswerable_done))
        if unanswerable_done else None,
        "correct_refusal_rate": (sum(1 for r in unanswerable_done if r["found"] is False) / len(unanswerable))
        if unanswerable else None,
        "latency_seconds": {"p50": percentile(seconds, .5), "p95": percentile(seconds, .95)},
        "usage": summarize_usage(rows),
        "clarification": summarize_clarification(completed),
        "grounding": summarize_grounding(found)}


def summarize_usage(rows) -> dict:
    """모델 호출·토큰 합계(#76). 무료 한도는 질문 수가 아니라 호출·토큰 기준이라 함께 기록한다."""
    measured = [r["usage"] for r in rows if r.get("usage")]
    if not measured:
        return {"measured": 0}
    total = {key: sum(int(u.get(key) or 0) for u in measured)
             for key in ("calls", "retries", "input_tokens", "output_tokens")}
    return {"measured": len(measured), **total,
            "calls_per_case": round(total["calls"] / len(measured), 2)}


def summarize_clarification(completed) -> dict:
    """조건 되묻기(#78). 기대 사례 중 물어본 비율과, 묻지 않아야 할 사례에서 불필요하게 물은 비율.

    답변 결과에 clarification 필드가 없으면(기능 이전 버전) 측정하지 않는다(None).
    """
    measured = [r for r in completed if r["clarification_asked"] is not None]
    if not measured:
        return {"measured": 0, "asked_when_needed": None, "unnecessary_rate": None}
    needed = [r for r in measured if r["expected_clarification"] is True]
    not_needed = [r for r in measured if r["expected_clarification"] is False]
    return {"measured": len(measured),
            "asked_when_needed": (sum(r["clarification_asked"] for r in needed) / len(needed)) if needed else None,
            "unnecessary_rate": (sum(r["clarification_asked"] for r in not_needed) / len(not_needed))
            if not_needed else None,
            "denominators": {"needed": len(needed), "not_needed": len(not_needed)}}


def summarize_grounding(found) -> dict:
    """근거를 찾았다고 답한 사례 중 수치 근거 검사를 통과한 비율과 고쳐 쓰기 횟수(#78)."""
    measured = [r for r in found if r["grounding_verified"] is not None]
    if not measured:
        return {"measured": 0, "verified_rate": None, "repaired": None}
    return {"measured": len(measured),
            "verified_rate": sum(1 for r in measured if r["grounding_verified"]) / len(measured),
            "repaired": sum(1 for r in measured if r["grounding_repaired"])}


def split_summaries(rows, summarize) -> dict:
    """조정용(tune)과 최종 확인용(holdout) 사례를 나눠 집계한다(#77). 같은 질문에만 맞춘 개선을 가려낸다."""
    splits = sorted({r.get("split") or "unspecified" for r in rows})
    return {split: summarize([r for r in rows if (r.get("split") or "unspecified") == split]) for split in splits}


def load_regulations(args):
    if args.snapshot:
        return json.loads(Path(args.snapshot).read_text(encoding="utf-8"))["regulations"]
    text = Path(args.corpus).read_text(encoding="utf-8")
    return [dict(DEFAULT_REGULATION, text=text)]


def load_env_file(path) -> list[str]:
    """.env에서 아직 없는 환경변수만 채운다(이미 설정한 값이 우선). 값은 출력하지 않는다."""
    path = Path(path)
    if not path.is_file():
        return []
    from dotenv import dotenv_values

    loaded = []
    for key, value in dotenv_values(path).items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standin", "real"), required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--corpus", default=str(ROOT / "tests/fixtures/hansung_rules_excerpt.txt"))
    parser.add_argument("--eval", default=str(ROOT / "eval/regulation_eval.jsonl"))
    parser.add_argument("--conflict-eval", default=str(ROOT / "eval/conflict_cases.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "eval/results"))
    parser.add_argument("--split", choices=("all", "tune", "holdout"), default="all",
                        help="평가할 사례 묶음(#77). 조정 중에는 tune만, 최종 판정은 holdout으로 확인한다.")
    parser.add_argument("--pause", type=float, default=0.0,
                        help="real 모드 답변 사례 사이 대기(초). Groq 무료(분당 8K 토큰)는 20 정도를 권한다(사례당 약 2~2.5K 토큰)")
    parser.add_argument("--skip-conflicts", action="store_true",
                        help="충돌 분석 모델 평가를 건너뛴다(긴 문서는 무료 분당 토큰 한도를 넘기 쉽다)")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="읽을 .env(이미 설정한 환경변수가 우선)")
    args = parser.parse_args()
    loaded = load_env_file(args.env_file)
    if loaded:
        print(".env에서 읽은 설정:", ", ".join(sorted(loaded)))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL(일회용 PostgreSQL)이 필요합니다. 평가는 색인을 비우고 다시 만듭니다.")

    regulations = load_regulations(args)
    cases = [json.loads(line) for line in Path(args.eval).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.split != "all":
        cases = [case for case in cases if case.get("split") == args.split]
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
            rows["hybrid"].append({"id": case["id"], "type": case["type"], "split": case.get("split"),
                                   "rank": case_rank(ids, expected, case.get("required_articles")), "top": ids[:3],
                                   "revision": next((h["revision_id"] for h in hits
                                                     if h["article_id"] in expected), None),
                                   "url_ok": not wrong_url})
            keyword = repository.keyword_candidates(query_terms(case["question"]), limit=max(KS))
            vector = repository.vector_candidates(embeddings.embed_query(case["question"]), limit=max(KS))
            required = case.get("required_articles")
            rows["keyword_only"].append({"rank": case_rank([h["article_id"] for h in keyword], expected, required)})
            rows["vector_only"].append({"rank": case_rank([h["article_id"] for h in vector], expected, required)})

    regressions = run_regressions(repository, embeddings, retriever, regulations)
    stage_errors = []
    try:
        conflicts = (conflict_evaluation(Path(args.conflict_eval), retriever, None) if args.skip_conflicts
                     else conflict_evaluation(Path(args.conflict_eval), retriever, llm))
        if args.skip_conflicts and llm is not None:
            not_measured["conflict_quality"] = "--skip-conflicts: 충돌 지적 정확도는 측정하지 않고 후보 재현율만 기록"
    except Exception as exc:  # 예상하지 못한 오류도 이미 계산한 검색 결과 저장을 막지 않는다(#58).
        stage_errors.append({"stage": "conflicts", "error": type(exc).__name__})
        conflicts = {"cases": 0, "candidate_recall_mean": None, "errors": 1, "per_case": []}

    answers = None
    if llm is None:
        not_measured["answer_quality"] = "실제 LLM이 없는 standin 모드: 인용 일치·무근거 답변·거절은 측정하지 않음"
        not_measured["conflict_quality"] = "실제 LLM이 없는 standin 모드: 충돌 지적 정확도는 측정하지 않고 후보 재현율만 기록"
    else:
        from hsrule.answering import CitedAnswerer
        try:
            answers = evaluate_answers(cases, CitedAnswerer(llm, retriever, k=settings.retrieval_k), regulations,
                                       pause=args.pause)
        except Exception as exc:
            stage_errors.append({"stage": "answers", "error": type(exc).__name__})
            not_measured["answer_quality"] = f"답변 평가 중 예상하지 못한 오류로 중단됨: {type(exc).__name__}"

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
            "eval_split": args.split,
            "embedding_model": embeddings.model_name, "chunk_params": {"max_tokens": 128, "overlap_tokens": 16},
            "tokenizer": type(embeddings.tokenizer).__name__ if args.mode == "standin" else embeddings.model_name,
            "llm_provider": settings.provider if settings else None,
            "llm_model": settings.model if settings else None,
            "prompt_sha256": hashlib.sha256(Path(ROOT / "hsrule/chains.py").read_bytes() +
                                            Path(ROOT / "hsrule/answering.py").read_bytes()).hexdigest()},
        "retrieval": {"hybrid": retrieval_metrics(rows["hybrid"]), "keyword_only": retrieval_metrics(rows["keyword_only"]),
                      "vector_only": retrieval_metrics(rows["vector_only"]),
                      "by_split": split_summaries(rows["hybrid"], retrieval_metrics),
                      "by_type": {t: retrieval_metrics([r for r in rows["hybrid"] if r["type"] == t])
                                  for t in sorted({r["type"] for r in rows["hybrid"]})},
                      # 실패 분류용(#77): 정답이 5위 안에 없던 사례와 대신 나온 상위 조항
                      "misses": [{"id": r["id"], "type": r["type"], "split": r["split"], "rank": r["rank"],
                                  "top": r["top"]} for r in rows["hybrid"] if not r["rank"] or r["rank"] > max(KS)],
                      "per_case": rows["hybrid"], "skipped": skipped,
                      "deleted_article_regressions": absent_failures,
                      "search_latency_seconds": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95)}},
        "answers": answers, "conflicts": conflicts, "regressions": regressions,
        "retrieval_limits": retriever.limits, "not_measured": not_measured, "errors": stage_errors,
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    path = Path(args.out) / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{args.mode}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mode", "claim")}, ensure_ascii=False))
    print(json.dumps(result["retrieval"]["hybrid"], ensure_ascii=False))
    print(json.dumps({"regressions": result["regressions"]["all_passed"],
                      "conflict_candidate_recall": result["conflicts"]["candidate_recall_mean"]},
                     ensure_ascii=False))
    if answers:
        summary = answers["summary"]
        print(json.dumps({"answers": {key: summary[key] for key in (
            "cases", "completed", "errors", "error_reasons", "citation_match_rate", "correct_refusal_rate",
            "unsupported_answer_rate", "latency_seconds", "usage", "clarification", "grounding")}},
            ensure_ascii=False))
    print("저장:", path)


if __name__ == "__main__":
    main()

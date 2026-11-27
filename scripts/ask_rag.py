"""공식 규정 RAG 로컬 질문 도구 (#76).

웹 API 없이 일회용 DB의 실제 검색 + 실제 임베딩 + API 모델로 질문하고, 답변·근거 조항·추가 질문·수치 검사·
호출 수·토큰·지연을 바로 확인한다. 기록(--log)은 실제 연결 결과를 남기는 용도다.

    .env(저장소 최상위, 커밋되지 않음)에 DATABASE_URL·GEMINI_API_KEY·GEMINI_MODEL을 적거나 export한다.
    python scripts/ask_rag.py --ping             # 키·모델 연결만 확인
    python scripts/ask_rag.py --index            # 학칙 발췌본을 색인하고 대화형으로 질문
    python scripts/ask_rag.py -q "졸업학점은?"   # 한 번만 질문

대화 명령: /new(새 대화) /en /ko(답변 언어) /private(비공개 포함 전환) /quit
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REGULATION = {"regulation_id": "20", "revision_id": "20:1623", "title": "한성대학교 학칙",
                      "official_url": "https://rule.hansung.ac.kr/lmxsrv/law/lawFullContent.do?SEQ=20&SEQ_HISTORY=1623"}
REASONS = {
    "timeout": "시간 안에 응답하지 않았습니다.",
    "rate_limit": "모델 호출 한도(무료 한도)를 넘었습니다. 잠시 뒤 다시 시도하세요.",
    "provider": "모델 호출에 실패했습니다. 키·모델 ID·네트워크를 확인하세요.",
    "json": "모델이 정해진 형식으로 답하지 않았습니다.",
}


def load_regulations(snapshot: str | None) -> list[dict]:
    """스냅샷(JSON {"regulations": [...]} 또는 JSONL)이 없으면 학칙 발췌본 하나를 쓴다(표본 데이터임을 출력에 표시)."""
    if not snapshot:
        text = (ROOT / "tests/fixtures/hansung_rules_excerpt.txt").read_text(encoding="utf-8")
        return [dict(DEFAULT_REGULATION, text=text)]
    raw = Path(snapshot).read_text(encoding="utf-8")
    if raw.lstrip().startswith("{") and '"regulations"' in raw[:2000]:
        return json.loads(raw)["regulations"]
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def format_result(result: dict, seconds: float) -> str:
    lines = [result["answer"], ""]
    if result.get("found"):
        for source in result["sources"]:
            lines.append(f"  [{source['label']}] {source.get('title', '')} {source['article_id']} "
                         f"(개정 {source['revision_id']}) {source.get('url', '')}")
    else:
        lines.append(f"  근거를 찾지 못함 ({result.get('reason', 'model')})")
    clarification = result.get("clarification") or {}
    if clarification.get("needed"):
        lines.append(f"  추가 질문 필요: {', '.join(clarification['missing']) or '조건'}")
    grounding = result.get("grounding") or {}
    if grounding and not grounding.get("verified", True):
        lines.append(f"  수치 확인 안 됨: {', '.join(grounding['unsupported'])}")
    elif grounding.get("repaired"):
        lines.append("  수치 불일치를 한 번 고쳐 씀")
    usage = result.get("usage") or {}
    lines.append(f"  {seconds:.2f}초 · 모델 호출 {usage.get('calls', 0)}회(재시도 {usage.get('retries', 0)}) · "
                 f"토큰 입력 {usage.get('input_tokens', 0)} / 출력 {usage.get('output_tokens', 0)}")
    if result.get("followups"):
        lines.append("  이어서 물어볼 만한 것: " + " / ".join(result["followups"]))
    return "\n".join(lines)


def run_session(answerer, questions, out, *, language="ko", include_private=False, log=None, meta=None):
    """질문을 차례로 처리한다. 대화 이력을 이어 가며, 오류가 나도 다음 질문을 계속 받는다."""
    from hsrule.answering import RagError

    history = []
    for raw in questions:
        question = raw.strip()
        if not question:
            continue
        if question == "/quit":
            break
        if question == "/new":
            history = []
            print("새 대화를 시작합니다.", file=out)
            continue
        if question in ("/en", "/ko"):
            language = question[1:]
            print(f"답변 언어: {language}", file=out)
            continue
        if question == "/private":
            include_private = not include_private
            print(f"비공개 규정 포함: {include_private}", file=out)
            continue
        started = time.perf_counter()
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "question": question,
                  "language": language, "include_private": include_private, "history_turns": len(history) // 2,
                  **(meta or {})}
        try:
            result = answerer.answer(question, list(history), include_private=include_private, language=language)
        except RagError as exc:
            seconds = time.perf_counter() - started
            print(f"오류({exc.reason}): {REASONS.get(exc.reason, str(exc))}", file=out)
            record.update(error={"code": exc.code, "reason": exc.reason}, seconds=round(seconds, 3))
        except ValueError as exc:
            print(f"입력 오류: {exc}", file=out)
            continue
        else:
            seconds = time.perf_counter() - started
            print(format_result(result, seconds), file=out)
            history += [{"role": "user", "content": question}, {"role": "assistant", "content": result["answer"]}]
            record.update(result=result, seconds=round(seconds, 3))
        print("", file=out)
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return history


def load_env_file(path) -> list[str]:
    """.env를 읽어 아직 없는 환경변수만 채운다(이미 export한 값이 우선). 값은 출력하지 않고 키 이름만 돌려준다."""
    path = Path(path)
    if not path.is_file():
        return []
    from dotenv import dotenv_values

    loaded = []
    for key, value in dotenv_values(path).items():
        if value is not None and value != "" and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def ping(llm) -> tuple[bool, str]:
    """작은 요청 하나로 키·모델·네트워크를 확인한다. 예외 본문은 출력하지 않는다(키·원문이 섞일 수 있음)."""
    from hsrule.chains import _status_code, is_rate_limited, is_timeout_error

    try:
        (llm.bind(timeout=30) if hasattr(llm, "bind") else llm).invoke("Reply with OK.")
        return True, "모델 연결 정상"
    except Exception as exc:
        status = _status_code(exc)
        if is_rate_limited(exc):
            return False, "호출 한도 초과(429). 잠시 뒤 다시 시도하거나 AI Studio에서 한도를 확인하세요."
        if is_timeout_error(exc):
            return False, "시간 초과. 네트워크(방화벽·프록시)를 확인하세요."
        if status in (401, 403):
            return False, (f"키 인증 실패({status}). GEMINI_API_KEY 값(앞뒤 공백·따옴표 없이)과 "
                           "AI Studio에서 키가 활성인지 확인하세요.")
        if status == 404:
            return False, "모델을 찾을 수 없습니다(404). GEMINI_MODEL이 AI Studio 모델 목록에 있는 ID인지 확인하세요."
        if status == 400:
            return False, "요청 거부(400). AI Studio에서 발급한 Gemini API 키인지, 모델 ID가 맞는지 확인하세요."
        return False, f"모델 호출 실패({type(exc).__name__}{f', HTTP {status}' if status else ''})."


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="공식 규정 RAG 로컬 질문 도구 (#76)")
    parser.add_argument("-q", "--question", help="한 번만 질문하고 끝낸다")
    parser.add_argument("--language", choices=("ko", "en"), default="ko")
    parser.add_argument("--private", action="store_true", help="비공개 규정도 검색한다")
    parser.add_argument("--index", action="store_true", help="질문 전에 규정을 색인한다(바뀐 규정만 다시 임베딩)")
    parser.add_argument("--snapshot", help="색인할 수집 스냅샷. 없으면 학칙 발췌본(표본)")
    parser.add_argument("--check-embeddings", action="store_true", help="임베딩 모델 길이·차원·한국어 사전검증")
    parser.add_argument("--log", help="질문·답변 기록을 JSONL로 덧붙일 파일")
    parser.add_argument("--ping", action="store_true", help="키·모델 연결만 확인하고 끝낸다")
    parser.add_argument("--no-ping", action="store_true", help="시작할 때 연결 확인 호출을 하지 않는다")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="읽을 .env 파일(기본: 저장소 최상위)")
    args = parser.parse_args(argv)

    from hsrule.rag_config import RagConfigError, RagSettings, create_llm

    loaded = load_env_file(args.env_file)
    if loaded:
        print(f".env에서 읽은 설정: {', '.join(sorted(loaded))}")
    try:
        settings = RagSettings.from_env()
    except RagConfigError as exc:
        print(f"모델 설정 오류: {exc}", file=sys.stderr)
        return 2
    llm = create_llm(settings)
    if args.ping or not args.no_ping:
        ok, message = ping(llm)
        print(f"{settings.provider}/{settings.model}: {message}")
        if args.ping or not ok:
            return 0 if ok else 3

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL이 없습니다. 일회용 PostgreSQL(pgvector) 주소를 지정하세요. 운영 DB는 쓰지 마세요.",
              file=sys.stderr)
        return 2

    from hsrule.answering import CitedAnswerer
    from hsrule.embeddings import LocalEmbeddings
    from hsrule.official_indexer import index_regulation, validate_embeddings
    from hsrule.official_repository import OfficialRepository
    from hsrule.official_retrieval import HybridRetriever

    embeddings = LocalEmbeddings()
    if args.check_embeddings or args.index:
        print("임베딩 사전검증:", json.dumps(validate_embeddings(embeddings), ensure_ascii=False))
    repository = OfficialRepository(url)
    repository.initialize()
    corpus = "스냅샷 " + args.snapshot if args.snapshot else "학칙 발췌본(표본, 실제 전체 규정 아님)"
    if args.index:
        for regulation in load_regulations(args.snapshot):
            result = index_regulation(regulation, repository, embeddings)
            print(f"색인 {regulation.get('regulation_id')} {regulation.get('title', '')}: {result['status']}")
    answerer = CitedAnswerer(llm, HybridRetriever(repository, embeddings, k=settings.retrieval_k),
                             k=settings.retrieval_k)
    meta = {"provider": settings.provider, "model": settings.model, "embedding_model": embeddings.model_name,
            "corpus": corpus, "commit": _commit()}
    print(f"모델 {settings.provider}/{settings.model} · 임베딩 {embeddings.model_name} · 데이터 {corpus}")
    if args.question:
        run_session(answerer, [args.question], sys.stdout, language=args.language, include_private=args.private,
                    log=args.log, meta=meta)
        return 0
    print("질문을 입력하세요. /new 새 대화, /en·/ko 언어, /private 비공개 포함, /quit 종료")

    def prompts():
        while True:
            try:
                yield input("질문> ")
            except EOFError:
                return

    run_session(answerer, prompts(), sys.stdout, language=args.language, include_private=args.private,
                log=args.log, meta=meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

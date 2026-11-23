"""한국어 키워드·벡터 결합 검색과 권한·개정 필터 (#26)."""
import re

ARTICLE_QUERY = re.compile(r"제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
HANGUL_OR_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
# 조사·어미를 어절 끝에서 한 번 떼어 내 키워드 일치율을 높인다(형태소 분석기는 쓰지 않는다).
SUFFIXES = ("에서는", "으로는", "에게는", "입니까", "인가요", "하나요", "할까요", "에서", "으로", "에게", "까지",
            "부터", "라고", "이란", "이며", "하는", "하면", "해요", "은", "는", "이", "가", "을", "를", "의", "에",
            "로", "과", "와", "도", "만", "인", "란", "나요", "요", "죠")
STOPWORDS = {"어떻게", "무엇", "뭐", "언제", "어디", "있나요", "되나요", "알려줘", "알려주세요", "대해", "관련", "규정", "학칙"}


def query_terms(query: str) -> list[str]:
    terms = []
    for word in HANGUL_OR_WORD.findall(query[:1024]):
        for suffix in SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                word = word[:-len(suffix)]
                break
        if len(word) >= 2 and word not in STOPWORDS and not word.isdigit():
            terms.append(word)
    return list(dict.fromkeys(terms))[:12]


def query_articles(query: str) -> list[str]:
    """질의 속 조 번호(제32조, 32조, 제3조의2)를 조항 ID 형식으로 정규화한다."""
    return list(dict.fromkeys(
        f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "") for m in ARTICLE_QUERY.finditer(query[:1024])
    ))[:5]


def fuse(rankings: list[tuple[str, float, list[dict]]], rrf_k: int = 60) -> list[dict]:
    """가중 RRF로 여러 검색 결과를 합친다. 각 후보에 어떤 검색이 찾았는지 `found_by`를 남긴다."""
    merged: dict[str, dict] = {}
    for name, weight, rows in rankings:
        for rank, row in enumerate(rows, 1):
            entry = merged.setdefault(row["id"], dict(row, fused_score=0.0, found_by=[], scores={}))
            entry["fused_score"] += weight / (rrf_k + rank)
            entry["found_by"].append(name)
            entry["scores"][name] = float(row["score"])
    return sorted(merged.values(), key=lambda e: (-e["fused_score"], e["id"]))


class HybridRetriever:
    def __init__(self, repository, embeddings, *, k: int = 6, candidates: int = 20,
                 weights: dict | None = None, expand_references: bool = True):
        self.repository, self.embeddings = repository, embeddings
        self.k, self.candidates, self.expand_references = k, candidates, expand_references
        self.weights = {"article": 3.0, "keyword": 1.0, "vector": 1.0, **(weights or {})}

    def search(self, query: str, *, include_private: bool = False, regulation_ids=None, revision_ids=None,
               k: int | None = None) -> list[dict]:
        scope = {"include_private": include_private, "regulation_ids": regulation_ids, "revision_ids": revision_ids}
        query = query.strip()
        if not query:
            return []
        rankings = []
        articles = query_articles(query)
        if articles:
            rankings.append(("article", self.weights["article"],
                             self.repository.article_candidates(articles, limit=self.candidates, **scope)))
        rankings.append(("keyword", self.weights["keyword"],
                         self.repository.keyword_candidates(query_terms(query), limit=self.candidates, **scope)))
        rankings.append(("vector", self.weights["vector"], self.repository.vector_candidates(
            self.embeddings.embed_query(query), limit=self.candidates, **scope)))
        hits = fuse(rankings)[:k or self.k]
        if self.expand_references:
            hits += self._referenced(hits, scope, k or self.k)
        return hits

    def _referenced(self, hits: list[dict], scope: dict, k: int) -> list[dict]:
        """상위 근거가 인용한 다른 조를 같은 개정 안에서 덧붙인다(교차 참조). 이미 있는 청크는 제외한다."""
        have = {hit["id"] for hit in hits}
        extra = []
        for hit in hits[:3]:
            wanted = [ref for ref in hit["refs"] if ref.startswith("제")]
            if not wanted:
                continue
            for row in self.repository.article_candidates(
                    wanted, limit=2 * len(wanted), include_private=scope["include_private"],
                    regulation_ids=[hit["regulation_id"]], revision_ids=[hit["revision_id"]]):
                if row["id"] not in have and len(extra) < max(2, k // 2):
                    have.add(row["id"])
                    extra.append(dict(row, fused_score=0.0, found_by=["reference"], scores={"reference": 1.0}))
        return extra

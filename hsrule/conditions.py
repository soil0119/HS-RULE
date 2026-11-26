"""규정 판단 조건 확인과 답변 근거 충실도 검사 (#78).

- 질문·대화 이력에서 사용자가 이미 밝힌 조건(입학년도·과정·학적 상태·학과·적용 시점)을 찾는다.
  모델에게 알려 주어 다시 묻지 않게 하고, 모델이 요청한 추가 질문을 정리한다.
- 답변의 수치가 인용한 근거(또는 사용자가 말한 값)에 있는지 확인한다.

모두 규칙 기반 점검이다. 문장 의미 전체를 검증하지 않으며, 한글 수사(예: '일곱 학기')는 검사하지 않는다.
"""
import re

CONDITIONS = ("admission_year", "program", "student_status", "department", "effective_date")
CONDITION_NAMES = {
    "ko": {"admission_year": "입학년도", "program": "과정", "student_status": "학적 상태",
           "department": "소속 학과·전공", "effective_date": "적용 시점"},
    "en": {"admission_year": "admission year", "program": "program", "student_status": "enrollment status",
           "department": "department/major", "effective_date": "applicable date"},
}
DEFAULT_QUESTIONS = {
    "ko": {"admission_year": "몇 학년도에 입학하셨나요?",
           "program": "학부·대학원·학석사연계과정 중 어느 과정인가요?",
           "student_status": "현재 학적 상태(재학·휴학·편입 등)는 어떻게 되나요?",
           "department": "소속 학과(전공)는 어디인가요?",
           "effective_date": "언제 기준으로 적용되는지 알려 주시겠어요?"},
    "en": {"admission_year": "Which academic year were you admitted in?",
           "program": "Are you in an undergraduate, graduate, or combined bachelor's-master's program?",
           "student_status": "What is your current enrollment status (enrolled, on leave, transfer, etc.)?",
           "department": "Which department or major are you in?",
           "effective_date": "As of which date should the rule apply?"},
}
MAX_CLARIFYING_QUESTIONS = 2

_YEAR = r"(?:19|20)\d{2}"
ADMISSION_PATTERNS = (
    re.compile(rf"({_YEAR})\s*(?:학번|학년도\s*(?:에\s*)?(?:입학|신입)|년\s*(?:에\s*)?입학|학년도\s*입학생|입학생)"),
    re.compile(r"(?<!\d)(\d{2})\s*학번"),
    re.compile(rf"(?:admitted|entered|enrolled|matriculated)\s+(?:in\s+)?({_YEAR})", re.I),
    re.compile(rf"({_YEAR})\s+(?:admission|entrant|intake)", re.I),
)
PROGRAM_PATTERNS = (
    ("학석사연계", re.compile(r"학\s*[·ㆍ.]?\s*석사\s*연계|bachelor'?s?[-\s]*master'?s?|combined\s+degree", re.I)),
    ("대학원", re.compile(r"대학원생?|석사과정|박사과정|graduate\s+(?:student|school|program)", re.I)),
    ("학부", re.compile(r"학부생|학부\s*과정|undergraduate", re.I)),
)
STATUS_PATTERN = re.compile(r"편입(?:생|학)?|재입학|휴학(?:생|\s*중)?|복학|외국인\s*(?:유학생)?|졸업\s*유예|재학\s*(?:생|중)"
                            r"|transfer\s+student|on\s+leave|returning|international\s+student", re.I)
DEPARTMENT_PATTERN = re.compile(r"([가-힣A-Za-z·ㆍ]{2,20}(?:학과|학부|전공))")
GENERIC_DEPARTMENTS = {"학과", "학부", "전공", "계약학과", "모든학과", "해당학과", "소속학과", "어느학과", "무슨학과", "무슨전공",
                       "어느전공", "타학과", "본학과", "학부또는학과", "주전공", "복수전공", "부전공", "연계전공", "대학원학과"}
EFFECTIVE_PATTERN = re.compile(rf"({_YEAR})\s*(?:년|학년도)(?!\s*(?:에\s*)?입학)(?:\s*\d\s*학기)?|이번\s*학기|올해|현재\s*기준|지금\s*기준"
                               r"|this\s+semester|this\s+year|as\s+of\s+now", re.I)
PERSONAL_INFO = re.compile(r"이름|성명|연락처|전화|휴대폰|핸드폰|주민|주소|이메일|e-?mail|비밀번호|계좌|학번\s*전체|학번\s*\d+\s*자리"
                           r"|password|phone|address|full\s+student\s+id|student\s+id\s+number", re.I)


def _admission_year(match_text: str) -> str:
    return match_text if len(match_text) == 4 else (f"20{match_text}" if int(match_text) <= 69 else f"19{match_text}")


def detect_conditions(texts) -> dict[str, list[str]]:
    """사용자 글에서 밝힌 조건을 찾는다. 반환: {조건: [값...]} (찾은 조건만)."""
    found: dict[str, list[str]] = {}

    def add(key, value):
        values = found.setdefault(key, [])
        if value not in values:
            values.append(value)

    for text in texts:
        if not text:
            continue
        for pattern in ADMISSION_PATTERNS:
            for match in pattern.finditer(text):
                add("admission_year", _admission_year(match.group(1)))
        for name, pattern in PROGRAM_PATTERNS:
            if pattern.search(text):
                add("program", name)
        for match in STATUS_PATTERN.finditer(text):
            add("student_status", re.sub(r"\s+", " ", match.group(0)).strip())
        for match in DEPARTMENT_PATTERN.finditer(text):
            name = match.group(1)
            if re.sub(r"\s", "", name) not in GENERIC_DEPARTMENTS and not name.startswith(("무슨", "어느", "어떤")):
                add("department", name)
        for match in EFFECTIVE_PATTERN.finditer(text):
            add("effective_date", re.sub(r"\s+", " ", match.group(0)).strip())
    if "학석사연계" in found.get("program", []) and "대학원" in found["program"]:
        found["program"].remove("대학원")  # '학석사연계과정생의 대학원 과목'처럼 함께 나온 경우
    return found


def format_conditions(found: dict, language: str) -> str:
    """프롬프트에 넣을 '확인된 조건' 한 줄. 찾은 조건이 없으면 빈 문자열."""
    if not found:
        return ""
    names = CONDITION_NAMES[language]
    items = ", ".join(f"{names[key]}: {', '.join(found[key])}" for key in CONDITIONS if key in found)
    head = "[사용자가 밝힌 조건]" if language == "ko" else "[Conditions stated by the user]"
    return f"\n\n{head} {items}"


def clarification(needs: bool, questions, missing, provided: dict, language: str) -> dict:
    """모델의 추가 질문 요청을 정리한다.

    - 이미 밝힌 조건과 알 수 없는 조건 이름은 missing에서 뺀다.
    - 개인정보(이름·연락처·학번 전체 등)를 묻는 질문은 버린다. 입학년도 질문은 허용한다.
    - 질문이 비었으면 남은 조건으로 기본 질문을 만든다. 남은 것이 없으면 추가 질문이 필요 없다.
    """
    missing = [key for key in dict.fromkeys(missing or []) if key in CONDITIONS and key not in provided]
    kept, dropped = [], []
    for question in questions or []:
        question = str(question).strip()
        if not question:
            continue
        (dropped if PERSONAL_INFO.search(question) else kept).append(question[:300])
    if needs and not kept:
        kept = [DEFAULT_QUESTIONS[language][key] for key in missing]
    needed = bool(needs and kept and (missing or not provided))
    return {"needed": needed, "questions": kept[:MAX_CLARIFYING_QUESTIONS] if needed else [],
            "missing": missing if needed else [], "dropped_personal_questions": len(dropped)}


def attach_questions(answer: str, questions, language: str) -> str:
    """웹 API가 구조화 필드를 아직 쓰지 않아도 사용자가 볼 수 있도록, 답변에 없는 추가 질문을 덧붙인다."""
    compact = re.sub(r"\s", "", answer)
    missing = [q for q in questions if re.sub(r"\s", "", q) not in compact]
    if not missing:
        return answer
    head = "판단에 필요한 정보를 알려 주세요:" if language == "ko" else "Please tell me the following so I can check:"
    return answer.rstrip() + "\n\n" + head + "\n" + "\n".join(f"- {q}" for q in missing)


# 근거 충실도 ---------------------------------------------------------------

_REFERENCE_NOISE = re.compile(
    r"제\s*\d+\s*(?:조(?:\s*의\s*\d+)?|항|호|장|절|관)|\[?S\d+\]?|Article\s+\d+(?:-\d+)?|Paragraph\s+\d+|Item\s+\d+"
    r"|별표\s*\d+|<\s*별표[^>]*>|[①-⑳]", re.I)
_LIST_MARKER = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|\(\d{1,2}\))\s+")
_NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d])")


def _normalize(number: str) -> str:
    value = float(number)
    return str(int(value)) if value == int(value) else repr(value)


def numbers_in(text: str, *, answer: bool = False) -> set[str]:
    """글 속 수치를 정규화해 모은다(4.0 → 4). 답변이면 조항 번호·인용 라벨·목록 번호를 뺀다."""
    if answer:
        text = _LIST_MARKER.sub(" ", _REFERENCE_NOISE.sub(" ", text))
    return {_normalize(n) for n in _NUMBER.findall(text)}


def check_grounding(answer: str, evidence_texts, user_texts) -> dict:
    """답변의 수치가 인용 근거나 사용자가 말한 값에 있는지 확인한다.

    반환: {"checked": 검사한 수치 수, "unsupported": [근거에 없는 수치], "verified": bool}
    """
    claimed = numbers_in(answer, answer=True)
    allowed = set()
    for text in list(evidence_texts) + list(user_texts):
        allowed |= numbers_in(text or "")
    unsupported = sorted(claimed - allowed, key=float)
    return {"checked": len(claimed), "unsupported": unsupported, "verified": not unsupported}


GROUNDING_NOTICE = {
    "ko": "※ 답변의 일부 수치({values})는 인용한 조항에서 확인되지 않았습니다. 정확한 내용은 담당 부서에 확인해 주세요.",
    "en": "Note: some figures in this answer ({values}) could not be found in the cited articles. "
          "Please confirm with the relevant office.",
}

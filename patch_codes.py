"""
기존 hansung_rules.json에 rule_code 필드 추가
lawFullView에서 규정 코드(1-0-1 등)만 빠르게 가져옴
"""
import requests, json, re, time
from pathlib import Path
from bs4 import BeautifulSoup
from crawler_config import load_cookies

BASE = "https://rule.hansung.ac.kr"
COOKIES = load_cookies()
session = requests.Session()
session.cookies.update(COOKIES)
session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})

rules = json.loads(Path("hansung_rules.json").read_text(encoding="utf-8"))
print(f"{len(rules)}개 규정 로드")

patched = 0
for i, r in enumerate(rules):
    if r.get("rule_code"):
        continue  # 이미 있으면 스킵

    seq  = r["seq"]
    hist = r["seq_history"]
    try:
        resp = session.get(f"{BASE}/lmxsrv/law/lawFullView.do?SEQ={seq}&SEQ_HISTORY={hist}", timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        stit = soup.select_one(".Stit")
        if stit:
            m = re.search(r'(\d+-\d+-\S+)', stit.get_text())
            if m:
                r["rule_code"] = m.group(1).strip()
                patched += 1
    except Exception as e:
        print(f"  SEQ={seq} 실패: {e}")

    if (i+1) % 50 == 0:
        Path("hansung_rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  중간저장 ({i+1}/{len(rules)}, 패치:{patched}개)")
    time.sleep(0.3)

Path("hansung_rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n완료! {patched}개 규정에 rule_code 추가됨")

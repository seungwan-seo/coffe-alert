"""점포라인(m.jumpoline.com) 카페 양도 매물 수집.

실측 (2026-08):
  GET m.jumpoline.com/jumpo_list_ajax.asp?s=jp&MCode=B&SCode=14&tabs=1&c1=11000&c2=
  → EUC-KR HTML 조각. 서울(c1=11000) 카페(B/14) 매물 100건.
  페이지네이션 파라미터는 먹히지 않음 (page=2도 같은 100건) — 최신 100건 고정.

목록에서 얻는 것: 매물ID·지역·업종·브랜드·층·면적·제목·부제목·권리금·월수익·권리회수기간
목록에 없는 것: 보증금·월세 → 상세(jumpo_view.asp)에서 가져온다.

점포라인은 상업 사이트다. 요청 간격을 넉넉히 두고 개인 알림 용도로만 쓸 것.
"""
import re

import requests

BASE = "https://m.jumpoline.com"
LIST = BASE + "/jumpo_list_ajax.asp"
VIEW = BASE + "/jumpo_view.asp?s=&WebJOfrsID="
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

SEOUL = "11000"     # c1 지역코드
CAFE = ("B", "14")  # MCode, SCode = 카페


class Blocked(Exception):
    pass


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Referer": BASE + "/"})

# 매물 카드 하나를 구분하는 앵커: chk_list value="{id}" ... 다음 카드 전까지
CARD_RE = re.compile(r'name="chk_list"\s+value="(\d+)"(.*?)(?=name="chk_list"\s+value="\d+"|$)', re.S)
FIELD_RES = {
    "region": re.compile(r'class="t_local">([^<]*)<'),
    "category": re.compile(r'class="t_mcate">([^<]*)<'),
    "brand": re.compile(r"class='franch_name'>([^<]*)<"),
    "floor": re.compile(r'class="stair">([^<]*)<'),
    "area": re.compile(r'class="floor">([^<]*)<'),
    "title": re.compile(r'class="tit">([^<]*)'),
    "subtitle": re.compile(r'class="bxsubtit"[^>]*>([^<]*)<'),
    "premium": re.compile(r'class="premium"><em>권리금</em>\s*([^<]*)<'),
    "profit": re.compile(r'class="mprofit"><em>월수익</em>\s*([^<]*?)(?:<|$)'),
    "payback": re.compile(r'<em>권리회수</em>\s*([^<]*)<'),
    "code": re.compile(r"매물번호\s*<b>([^<]*)</b>"),
}
# 상세 페이지는 dt/dd 쌍으로 항목을 나열한다. 라벨 → 우리가 쓸 키.
# (보증금·월세는 상세에도 정형 필드로 없다 — 목록·상세 공통 한계)
DETAIL_LABELS = {
    "업종": "category",
    "도로명 주소": "address",
    "권리금": "premium",
    "창업비용": "startup_cost",     # 총 투자 규모
    "월수익": "monthly_profit",
    "손익분기점": "breakeven",
    "권리회수기간": "payback",
    "총인테리어": "interior_cost",   # 시설 가치 추정 — STRATEGY 시설 10점의 근거
}
DT_DD_RE = re.compile(r"<dt[^>]*>(.{0,60}?)</dt>\s*<dd[^>]*>(.{0,140}?)</dd>", re.S)
TH_TD_RE = re.compile(r"<th[^>]*>(.{0,50}?)</th>\s*<td[^>]*>(.{0,140}?)</td>", re.S)
MONEY_KEYS = {"premium", "startup_cost", "monthly_profit", "breakeven", "interior_cost"}


def _get(url: str, **kw) -> str:
    resp = SESSION.get(url, timeout=25, **kw)
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} from {url}")
    resp.raise_for_status()
    resp.encoding = "euc-kr"
    return resp.text


def _num(text: str):
    """'600만' / '3,680' → 만원 단위 정수. 억 표기도 처리."""
    if not text:
        return None
    t = text.strip()
    eok = re.search(r"([\d,]+)\s*억", t)
    man = re.search(r"([\d,]+)\s*만", t)
    total = 0
    if eok:
        total += int(eok.group(1).replace(",", "")) * 10000
        if man:
            total += int(man.group(1).replace(",", ""))
        return total
    if man:
        return int(man.group(1).replace(",", ""))
    plain = re.search(r"([\d,]{2,})", t)
    return int(plain.group(1).replace(",", "")) if plain else None


def list_seoul_cafes() -> list:
    """서울 카페 매물 목록 (최신 100건)."""
    html = _get(LIST, params={
        "s": "jp", "MCode": CAFE[0], "SCode": CAFE[1],
        "tabs": "1", "c1": SEOUL, "c2": "",
    })
    if "t_local" not in html:
        raise Blocked("점포라인 목록에 매물 마크업이 없음 (구조 변경 또는 차단)")

    out = []
    for item_id, chunk in CARD_RE.findall(html):
        row = {"id": f"jp{item_id}", "raw_id": item_id, "url": VIEW + item_id}
        for key, rx in FIELD_RES.items():
            m = rx.search(chunk)
            row[key] = (m.group(1).strip() if m else "")
        row["premium_manwon"] = _num(row["premium"])
        out.append(row)
    return out


def _clean(html_fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html_fragment)
    t = t.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def fetch_detail(raw_id: str) -> dict:
    """상세에서 창업비용·인테리어비·월수익·주소 등을 보강한다. 실패해도 빈 dict."""
    try:
        html = _get(VIEW + raw_id)
    except (requests.RequestException, Blocked):
        return {}

    out = {}
    for dt, dd in DT_DD_RE.findall(html) + TH_TD_RE.findall(html):
        label = _clean(dt).rstrip(":").strip()
        key = DETAIL_LABELS.get(label) or DETAIL_LABELS.get(label.lstrip("123. "))
        if not key or key in out:
            continue
        value = _clean(dd)
        if "표시제외" in value:      # 판매자가 비공개 처리한 항목
            continue
        out[key] = _num(value) if key in MONEY_KEYS else value
    return out

"""당근부동산(realty.daangn.com) 수집.

데이터 소스 (2026-08 실측):
- 상세: window.RELAY_STORE 임베디드 JSON (이중 인코딩). 404 = 삭제/거래완료.
- 빠른 감지: /map/서울특별시/{구}/{카테고리} SSR 페이지 (구당 최신·추천 20건, CDN 캐시 1시간)
- 백스톱: /sitemap-articles/1 (article ID 내림차순, 하루 1회 재생성)
"""
import json
import re
import time
from urllib.parse import quote

import requests

BASE = "https://realty.daangn.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

SEOUL_GU = [
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
    "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
    "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구",
]

CATEGORY_KO = {
    "SPLIT_ONE_ROOM": "원룸", "OPEN_ONE_ROOM": "원룸", "TWO_ROOM": "빌라",
    "APART": "아파트", "OFFICETEL": "오피스텔", "STORE": "상가",
    "OFFICE": "사무실", "LAND": "토지", "BUILDING": "건물",
    "HOUSE": "주택",   # 실측으로 뒤늦게 발견한 값 (2026-08-21)
}

TRADE_KO = {"MONTH": "월세", "BORROW": "전세", "BUY": "매매", "SHORT": "단기"}


class Blocked(Exception):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


SESSION = _session()


def _get(url: str, **kw) -> requests.Response:
    resp = SESSION.get(url, timeout=20, **kw)
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} from {url}")
    return resp


def fast_lane_ids(categories: list, delay: float) -> set:
    """서울 25개 구의 카테고리 페이지에서 노출 중인 article ID(int)를 모은다."""
    ids = set()
    for gu in SEOUL_GU:
        for cat in categories:
            url = f"{BASE}/map/{quote('서울특별시')}/{quote(gu)}/{quote(cat)}"
            resp = _get(url)
            if resp.status_code == 200:
                ids.update(int(m) for m in re.findall(r'href="/articles/(\d+)"', resp.text))
            time.sleep(delay)
    return ids


def sitemap_new_ids(state: dict) -> list:
    """sitemap-articles/1에서 지난번 이후 새로 등록된 ID를 내림차순으로 반환.
    변경 없으면(304) 빈 리스트."""
    headers = {}
    if state["sitemap_last_modified"]:
        headers["If-Modified-Since"] = state["sitemap_last_modified"]
    resp = _get(f"{BASE}/sitemap-articles/1", headers=headers)
    if resp.status_code == 304:
        return []
    resp.raise_for_status()
    ids = [int(i) for i in re.findall(r"articles/(\d+)", resp.text)]
    if not ids:
        # 빈/이상 응답으로 If-Modified-Since 기준점을 오염시키지 않는다
        return []
    state["sitemap_last_modified"] = resp.headers.get("Last-Modified", "")
    max_prev = state["realty_max_sitemap_id"]
    state["realty_max_sitemap_id"] = max(max(ids), max_prev)
    if max_prev == 0:  # 첫 실행: 기준점만 기록
        return []
    return sorted((i for i in ids if i > max_prev), reverse=True)


def fetch_title(article_id) -> str | None:
    """상세 페이지의 <title>만 스트리밍으로 읽는다 (백스톱 레인의 1차 필터용).
    404(삭제)면 None."""
    resp = SESSION.get(f"{BASE}/articles/{article_id}", timeout=20, stream=True)
    try:
        if resp.status_code == 404:
            return None
        if resp.status_code in (403, 429):
            raise Blocked(f"{resp.status_code} on article {article_id}")
        resp.raise_for_status()
        buf = b""
        for chunk in resp.iter_content(8192):
            buf += chunk
            if b"</title>" in buf or len(buf) > 131072:
                break
        m = re.search(rb"<title>(.*?)</title>", buf, re.S)
        if not m:
            # 200인데 title이 없으면 챌린지/이상 페이지 — 삭제(404)와 달리 소비하면 안 됨
            raise Blocked(f"200 without <title> on article {article_id}")
        return m.group(1).decode("utf-8", "replace")
    finally:
        resp.close()


def _parse_relay(html: str) -> dict:
    i = html.find("window.RELAY_STORE =")
    if i < 0:
        raise ValueError("no RELAY_STORE")
    outer, _ = json.JSONDecoder().raw_decode(html, html.find('"', i))
    return json.loads(outer)


def fetch_article(article_id) -> dict | None:
    """상세 페이지 전체를 파싱한다. 삭제/판매중 아님이면 None."""
    resp = _get(f"{BASE}/articles/{article_id}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    store = _parse_relay(resp.text)
    root = store["client:root"]
    key = next((k for k in root if k.startswith("articleByOriginalArticleId(")), None)
    ref = root.get(key) if key else None
    if not ref:
        return None
    art = store[ref["__ref"]]
    if art.get("status") != "ON_GOING" or art.get("isHide"):
        return None

    sales_type = store[art["salesTypeV3"]["__ref"]]["type"] if art.get("salesTypeV3") else ""
    trades = [store[r] for r in art["trades"]["__refs"]] if art.get("trades") else []
    region = store[art["region"]["__ref"]] if art.get("region") else {}
    biz = store[art["bizProfile"]["__ref"]].get("name") if art.get("bizProfile") else None
    image = None
    if (art.get("images") or {}).get("__refs"):
        image = store[art["images"]["__refs"][0]].get("url")

    return {
        "id": str(art.get("originalId") or article_id),
        "url": f"{BASE}/articles/{article_id}",
        "category": CATEGORY_KO.get(sales_type, sales_type),
        "trades": trades,
        "address": art.get("publicAddress") or art.get("address") or "",
        "region": " ".join(filter(None, [region.get("name1"), region.get("name2"), region.get("name3")])),
        "content": art.get("content") or "",
        "area_m2": art.get("area"),
        "floor": art.get("floor"),
        "premium_money": art.get("premiumMoney"),
        "manage_cost": art.get("totalManageCost"),
        "published_at": art.get("publishedAt", ""),
        "broker": biz,
        "image": image,
    }


def trade_snapshot(trades: list) -> dict:
    """가격 추적용 대표 거래조건 스냅샷 (만원 단위)."""
    if not trades:
        return {}
    t = next((x for x in trades if x.get("preferred")), trades[0])
    return {
        "type": t.get("type"),
        "deposit": t.get("deposit"),
        "monthlyPay": t.get("monthlyPay"),
        "price": t.get("price"),
    }


def is_price_drop(old: dict, new: dict) -> bool:
    """대표 거래조건이 같은 유형이면서 어떤 금액도 오르지 않고 하나 이상 내렸으면 True."""
    if not old or not new or old.get("type") != new.get("type"):
        return False
    dropped = raised = False
    for key in ("deposit", "monthlyPay", "price"):
        o, n = old.get(key), new.get(key)
        if o is None or n is None:
            continue
        if n < o:
            dropped = True
        elif n > o:
            raised = True
    return dropped and not raised


def fmt_manwon(v) -> str:
    """만원 단위 정수 → '3억 4,500' 스타일."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return "?"
    if v >= 10000:
        eok, rest = divmod(v, 10000)
        return f"{eok}억 {rest:,}" if rest else f"{eok}억"
    return f"{v:,}"


def fmt_manwon_full(v) -> str:
    """만원 단위 정수 → 단위 접미 포함 완성형: '2억원', '1억 5,000만원', '4,000만원'."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return "?"
    if v >= 10000:
        eok, rest = divmod(v, 10000)
        return f"{eok}억 {rest:,}만원" if rest else f"{eok}억원"
    return f"{v:,}만원"


def fmt_trade(trades: list) -> str:
    if not trades:
        return "가격 미상"
    t = next((x for x in trades if x.get("preferred")), trades[0])
    kind = TRADE_KO.get(t.get("type"), t.get("type", ""))
    if t.get("type") in ("MONTH", "SHORT"):
        return f"{kind} {fmt_manwon(t.get('deposit'))}/{fmt_manwon(t.get('monthlyPay'))}"
    if t.get("type") == "BORROW":
        return f"{kind} {fmt_manwon(t.get('deposit'))}"
    if t.get("type") == "BUY":
        return f"{kind} {fmt_manwon(t.get('price'))}"
    return kind

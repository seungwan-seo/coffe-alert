"""당근 중고거래(www.daangn.com) 키워드 검색.

Remix 데이터 라우트(?_data=routes/kr.buy-sell._index)로 JSON을 직접 받는다 (2026-08 실측).
검색은 동(洞) 단위로만 스코프되므로 서울 커버리지는 동 목록 순회로 만든다.
- User-Agent 필수 (없으면 403)
- HTTP/1.1 유지 (HTTP/2는 차단 보고 있음 — requests는 기본이 1.1이라 안전)
- 매 요청에 in=을 명시 (search_region 쿠키가 지역을 고정하는 것 방지)
"""
import time

import requests

BASE = "https://www.daangn.com"
DATA_ROUTE = "routes/kr.buy-sell._index"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class Blocked(Exception):
    pass


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def search(keyword: str, region_id: int) -> list:
    resp = SESSION.get(
        f"{BASE}/kr/buy-sell/",
        params={"in": region_id, "search": keyword, "_data": DATA_ROUTE},
        timeout=15,
    )
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} on buysell search '{keyword}'")
    resp.raise_for_status()
    data = resp.json()
    articles = (data.get("allPage") or {}).get("fleamarketArticles")
    if articles is None:  # 200이지만 구조가 없으면 소프트 차단으로 간주
        raise Blocked(f"empty structure on buysell search '{keyword}'")

    items = []
    for a in articles:
        try:
            price = int(float(a["price"])) if a.get("price") else None
        except (TypeError, ValueError):
            price = None
        href = a.get("href") or ""
        # href는 '/kr/buy-sell/{제목슬러그}-{고유코드}/' — 제목이 수정되면 슬러그가 바뀌므로
        # 중복 방지 키는 끝의 고유코드만 쓴다
        stable_id = href.rstrip("/").rsplit("-", 1)[-1] if href else a.get("id")
        items.append({
            "id": stable_id,
            "url": BASE + (a.get("href") or ""),
            "title": a.get("title") or "",
            "content": a.get("content") or "",
            "price": price,
            "thumbnail": a.get("thumbnail"),
            "status": a.get("status"),
            "region": (a.get("region") or {}).get("name", ""),
            "created_at": a.get("createdAt", ""),
        })
    return items


def region_meta(region_id: int) -> dict:
    """해당 지역 id의 메타데이터(시/구/동, 같은 구의 동 목록). discover_regions.py에서 사용."""
    resp = SESSION.get(
        f"{BASE}/kr/buy-sell/",
        params={"in": region_id, "_data": DATA_ROUTE},
        timeout=15,
    )
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} on region {region_id}")
    resp.raise_for_status()
    data = resp.json()
    region = data.get("region") or {}
    siblings = ((data.get("depth3RegionWithSiblings") or {}).get("siblingRegions")) or []
    return {
        "resolved_id": int(region.get("id", 0) or 0),
        "sido": region.get("depth1RegionName", ""),
        "gu": region.get("depth2RegionName", ""),
        "dong": region.get("depth3RegionName", ""),
        "siblings": [
            {"id": int(s["id"]), "sido": s.get("name1", ""), "gu": s.get("name2", ""), "dong": s.get("name3", "")}
            for s in siblings
            if s.get("id")
        ],
    }


def fmt_price(price) -> str:
    if price is None:
        return "가격문의"
    if price < 10000 or price % 10000:
        return f"{price:,}원"   # 만원 단위가 아니면 원 단위 그대로 (절사 방지)
    man = price // 10000
    if man >= 10000:
        eok, rest = divmod(man, 10000)
        return f"{eok}억 {rest:,}만원" if rest else f"{eok}억원"
    return f"{man:,}만원"


def polite_sleep(delay: float) -> None:
    time.sleep(delay)

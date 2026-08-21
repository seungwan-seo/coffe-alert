"""설정 기반 필터링 로직."""
from datetime import datetime, timedelta, timezone


def _keyword_hit(text: str, keywords: list, excludes: list) -> bool:
    for phrase in excludes:
        text = text.replace(phrase, "")
    return any(kw in text for kw in keywords)


def match_realty(cfg: dict, listing: dict):
    """listing: daangn_realty.fetch_article() 결과.
    통과하면 매칭된 규칙 이름, 아니면 None."""
    r = cfg["realty"]
    address = listing.get("address") or ""
    if r["regions"] and not any(address.startswith(reg) for reg in r["regions"]):
        return None

    # 구 페이지 큐레이션이 바뀌며 한참 전 매물이 갑자기 노출될 수 있다 — 오래된 건 조용히 넘어감
    freshness_days = r.get("freshness_days") or 0
    published = listing.get("published_at") or ""
    if freshness_days and published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - dt > timedelta(days=freshness_days):
                return None
        except ValueError:
            pass

    if r.get("unfiltered"):
        return "무필터"

    text = listing.get("content") or ""
    if listing.get("premium_money"):
        text += f"\n권리금 {listing['premium_money']}만원"
    category = listing.get("category") or ""
    for rule in r["rules"]:
        if rule["categories"] and category not in rule["categories"]:
            continue
        if _keyword_hit(text, rule["keywords"], rule.get("exclude_keywords", [])):
            return rule["name"]
    return None


def match_buysell(search_cfg: dict, item: dict, freshness_days: float = 3) -> str:
    """item: daangn_buysell.search() 결과의 아이템. 판정을 반환한다:
    - "match": 알림 대상
    - "perm":  영구 탈락 (판매완료, 등록 오래됨) → seen 처리해도 됨
    - "temp":  일시 탈락 (예약중, 이 검색어의 가격 미달) → seen 처리 금지.
               다른 검색어나 다음 순회에서 재평가되며, freshness가 지나면 perm으로 수렴한다.
    freshness_days 게이트가 없으면 첫 순회 내내 오래된 기존 매물이 전부 신규로 알림된다."""
    if item.get("status") == "Closed":
        return "perm"
    created = item.get("created_at") or ""
    if freshness_days and created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - dt > timedelta(days=freshness_days):
                return "perm"
        except ValueError:
            pass
    if item.get("status") != "Ongoing":   # Reserved 등 — 풀릴 수 있음
        return "temp"
    min_price = search_cfg.get("min_price") or 0
    if min_price and item.get("price") is not None and item["price"] < min_price:
        return "temp"
    # 당근 검색은 키워드 없는 연관 상품도 섞어 준다 — 키워드(또는 별칭) 토큰이
    # 제목/본문에 실제로 있어야 알림. aliases로 표기 변형(커피 머신/에스프레소 머신 등)을 인정한다.
    text = f"{item.get('title') or ''} {item.get('content') or ''}"
    phrases = [search_cfg["keyword"]] + (search_cfg.get("aliases") or [])
    for phrase in phrases:
        tokens = phrase.split()
        if tokens and all(t in text for t in tokens):
            return "match"
    return "temp"


def prefilter_title(cfg: dict, title: str) -> bool:
    """상세 페이지를 통째로 받기 전, <title>만으로 지역/종류 1차 판정.
    title 예: '서울특별시 관악구 봉천동 76.3㎡ 상가 월세 2,000 / 200만원 | 당근부동산'"""
    r = cfg["realty"]
    if r["regions"] and not any(title.startswith(reg) for reg in r["regions"]):
        return False
    if r.get("unfiltered"):
        return True
    # 모든 규칙이 카테고리를 지정했을 때만 카테고리로도 거른다
    cats = set()
    for rule in r["rules"]:
        if not rule["categories"]:
            return True
        cats.update(rule["categories"])
    return any(f" {cat} " in title for cat in cats)

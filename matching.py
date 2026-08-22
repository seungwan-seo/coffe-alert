"""설정 기반 필터링 로직."""
import re
from datetime import datetime, timedelta, timezone

# "음식점,카페,플라워샵은 안됩니다" / "카페 업종은 불가" 처럼 업종을 금지하는 문구를 걸러내기 위한 것.
# 키워드 바로 뒤(같은 절, 10자 이내)에 부정 표현이 오면 그 등장은 매칭으로 치지 않는다.
# 창을 좁게 잡은 이유: "카페 추천 자리, 흡연은 안됩니다"처럼 뒤쪽의 무관한 부정문까지
# 삼켜서 진짜 매물을 놓치는 게, 금지 매물 하나 더 오는 것보다 손해가 크다.
NEGATION_RE = re.compile(r"안\s*되|안\s*됩|안됨|불가|제외|금지|못\s*하|힘듭니다|어렵습니다")
NEGATION_WINDOW = 12
# 키워드와 부정어 사이에 이런 말이 끼어 있으면 그 부정은 다른 얘기로 본다
POSITIVE_RE = re.compile(r"추천|가능|자리|창업|운영|양도|영업|인수|매출")
CLAUSE_SPLIT_RE = re.compile(r"[\n.!?;]+")

# "추천업종 : 판매점, 사무실, 공방, 무인카페, 의류샵 등" 처럼 중개사가 나열한
# 업종 목록에 키워드가 끼어 있는 경우. 빈 상가 광고의 상투 문구라 매물 성격과 무관하다.
# 2026-08-23 실측: 알림 242건 중 38건(16%)이 이 패턴 하나 때문에 걸렸다.
RECOMMEND_RE = re.compile(r"추천\s*업종|업종\s*추천|가능\s*업종|권장\s*업종|업종\s*안내|다용도")
RECOMMEND_LOOKBACK = 60


def rented_trade(listing: dict, allowed=None) -> dict:
    """금액 판단에 쓸 임대 거래를 고른다.
    preferred를 쓰면 안 된다 — 매매+월세 동시 등록 매물에서 preferred가 매매일 수 있어
    월세 검사를 통째로 건너뛴다."""
    trades = listing.get("trades") or []
    kinds = set(allowed or ["MONTH"])
    return next((t for t in trades if t.get("type") in kinds), {})


def rent_manwon(listing: dict, allowed=None):
    """임대 거래의 월세(만원). 없으면 None."""
    return rented_trade(listing, allowed).get("monthlyPay")


def deposit_manwon(listing: dict, allowed=None):
    return rented_trade(listing, allowed).get("deposit")


def budget_tag(cfg: dict, listing: dict) -> str:
    """예산 라벨. 거르지 않고 표시만 한다."""
    b = (cfg.get("realty") or {}).get("budget") or {}
    if not b:
        return ""
    allowed = (cfg.get("realty") or {}).get("trade_types")
    monthly = rent_manwon(listing, allowed)
    if monthly is None:
        return "⚪ 금액미상"
    deposit = deposit_manwon(listing, allowed) or 0
    premium = listing.get("premium_money") or 0
    if (monthly <= b.get("starter_monthly", 60)
            and deposit <= b.get("starter_deposit", 1000)
            and premium <= b.get("starter_premium", 300)):
        return "💚 소액창업"
    if monthly <= b.get("review_monthly", 120):
        return "🟡 검토"
    return "🔴 예산초과"


def _keyword_hit(text: str, keywords: list, excludes: list) -> bool:
    for phrase in excludes:
        text = text.replace(phrase, "")
    for clause in CLAUSE_SPLIT_RE.split(text):
        for kw in keywords:
            for m in re.finditer(re.escape(kw), clause):
                # 업종 추천 목록 안의 등장은 무시 (빈 상가 광고 상투구)
                back = clause[max(0, m.start() - RECOMMEND_LOOKBACK):m.start()]
                if RECOMMEND_RE.search(back):
                    continue
                window = clause[m.end():m.end() + NEGATION_WINDOW]
                neg = NEGATION_RE.search(window)
                if not neg or POSITIVE_RE.search(window[:neg.start()]):
                    return True   # 부정 문맥도 추천목록도 아닌 등장이 하나라도 있으면 매칭
    return False


def match_realty(cfg: dict, listing: dict):
    """listing: daangn_realty.fetch_article() 결과.
    통과하면 매칭된 규칙 이름, 아니면 None."""
    r = cfg["realty"]
    address = listing.get("address") or ""
    if r["regions"] and not any(address.startswith(reg) for reg in r["regions"]):
        return None

    # 거래 유형 필터 — 세를 얻는 게 목적이라 매매(BUY)는 제외한다
    allowed = r.get("trade_types")
    if allowed:
        trades = listing.get("trades") or []
        kinds = {t.get("type") for t in trades}
        if kinds and not (kinds & set(allowed)):
            return None

    # 월세 상한 — 하루 50잔(월 공헌이익 195만원)으로도 계산이 안 서는 구간은 버린다
    cap = r.get("max_monthly_manwon") or 0
    monthly = rent_manwon(listing, allowed)
    if cap and monthly is not None and monthly > cap:
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

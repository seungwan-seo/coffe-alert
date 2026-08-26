"""설정 기반 필터링 로직."""
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

# ── 입지 데이터 (2026-08-24 서울 무인카페 2019~22 개업 487건 장기생존/조기폐업 분석 기반) ──
# 갈린 변수: ① 학교 450m + 아파트 600m 2,000세대: 둘 다 75%, 학교만 65%, 세대만 43%, 둘 다 없음 46%
#           ② 행정동 1층 평당 환산임대료 상위 1/3(≥15.6만)이면 52% vs 76%
#           ③ 고임대 상업지 + 역 364m 안 = 37% (역세권은 고임대권에서만 나쁘다; 저임대권에선 80 vs 80)
# 안 갈린 변수: 무인카페 이웃 수, 동 인구, 층, '주소상 아파트 단지 상가 여부', 저가 프랜차이즈, 대학, **대형병원**
# 주의: 주소상 상가 플래그와 실제 주변 세대수는 다른 변수다. 후자는 학교와 결합될 때 차이가 났다.
# → 🏥 병원세권 라벨은 근거가 없어 제거했다(병원 600m 내 58%, 오히려 낮은 쪽).
_HERE = os.path.dirname(__file__)


def _load_json(name, default):
    try:
        with open(os.path.join(_HERE, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


SCHOOLS = _load_json("schools.json", [])          # 초중고 2,131곳 (OSM, 서울)
APARTMENTS = _load_json("apartments.json", [])    # 서울 공동주택 2,807단지 (서울 열린데이터광장)
SUBWAY = _load_json("subway.json", [])            # 지하철역 369곳 (서울시 역사마스터, 역명 기준 중복 제거)
DONG_RENT = _load_json("dong_rent.json", {})      # 행정동코드(8자리) → {name, gu, rent(원/3.3㎡/월, 1층 환산), q}
DONG_BOUNDARY = _load_json("dong_boundary.json", [])   # 행정동 외곽 링 (점-폴리곤용)


def _meters(lat1, lon1, lat2, lon2) -> float:
    """두 좌표 사이 거리(m). 서울 규모에서는 평면 근사로 충분하다."""
    dlat = (lat2 - lat1) * 111_320
    dlon = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def _coords(listing: dict):
    try:
        return float(listing.get("lat")), float(listing.get("lon"))
    except (TypeError, ValueError):
        return None


def _nearest(listing: dict, points: list):
    """(가장 가까운 점, 거리 m). 좌표가 없으면 (None, None)."""
    c = _coords(listing)
    if not c or not points:
        return None, None
    best = min(points, key=lambda p: _meters(c[0], c[1], p["lat"], p["lon"]))
    return best, round(_meters(c[0], c[1], best["lat"], best["lon"]))


def nearest_school(listing: dict):
    return _nearest(listing, SCHOOLS)


def nearest_station(listing: dict):
    return _nearest(listing, SUBWAY)


@lru_cache(maxsize=4096)
def _apartment_households_at(lat: float, lon: float, radius_m: float) -> int:
    return sum(
        int(a.get("hh") or 0)
        for a in APARTMENTS
        if _meters(lat, lon, a["lat"], a["lon"]) <= radius_m
    )


def apartment_households(listing: dict, radius_m: float = 600):
    """매물 반경 안 공동주택 세대수. 좌표나 데이터가 없으면 None."""
    c = _coords(listing)
    if not c or not APARTMENTS:
        return None
    return _apartment_households_at(c[0], c[1], radius_m)


def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """레이 캐스팅. ring은 [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def dong_of(listing: dict):
    """좌표가 속한 행정동 {code, name, gu}. 없으면 None."""
    c = _coords(listing)
    if not c:
        return None
    lat, lon = c
    for d in DONG_BOUNDARY:
        for r in d["rings"]:
            b = r["bbox"]
            if b[0] <= lon <= b[2] and b[1] <= lat <= b[3] and _point_in_ring(lon, lat, r["ring"]):
                return {"code": d["code"], "name": d["name"], "gu": d["gu"]}
    return None


def dong_rent(listing: dict):
    """행정동 1층 평당 월 환산임대료(원)와 동 이름. 없으면 (None, None).
    환산임대료 = 보증금×12%/12 + 월세. 서울신용보증재단 추정치(우리마을가게 상권분석)."""
    d = dong_of(listing)
    if not d:
        return None, None
    r = DONG_RENT.get(d["code"])
    if not r:
        return None, d["name"]
    return r["rent"], d["name"]


def listing_rent_per_py(cfg: dict, listing: dict):
    """이 매물의 평당 월 환산임대료(만원). 동 평균과 같은 환산식(보증금×12%/12 + 월세)이라
    직접 비교 가능 — "시세 대비 싸게 나왔나"가 인수 전략의 핵심 신호다.
    동 평균이 1층 기준이라 1층 매물만, 면적 이상치(3~30평 밖)와 결측은 None."""
    r = (cfg.get("realty") or {})
    try:
        if float(listing.get("floor")) != 1.0:   # floor는 문자열 '1.0'/'0.5'(반지하)/None
            return None
    except (TypeError, ValueError):
        return None
    try:
        py = float(listing.get("area_m2")) / 3.3058
    except (TypeError, ValueError):
        return None
    if not (r.get("compare_py_min", 3.0) <= py <= r.get("compare_py_max", 30.0)):
        return None
    allowed = r.get("trade_types")
    monthly = rent_manwon(listing, allowed)
    if monthly is None:
        return None
    deposit = deposit_manwon(listing, allowed) or 0
    return (monthly + deposit * 0.01) / py


def location_tags(cfg: dict, listing: dict):
    """입지 라벨 목록과 상세 줄.
    🏘️ 대단지학교권 / 🏫 학교권 / 💸 저임대 생활권 / 💰 고임대 상업지 / 🚫 위험입지"""
    r = (cfg.get("realty") or {})
    tags, lines = [], []
    school, sd = nearest_school(listing)
    station, td = nearest_station(listing)
    rent, dong = dong_rent(listing)
    apartment_radius = r.get("apartment_radius_m", 600)
    households = apartment_households(listing, apartment_radius)
    school_near = sd is not None and sd <= r.get("school_radius_m", 450)
    dense_apartments = households is not None and households >= r.get("apartment_min_households", 2000)
    if school_near and dense_apartments:
        tags.append("🏘️ 대단지학교권")
    elif school_near:
        tags.append("🏫 학교권")
    if sd is not None and sd <= r.get("school_show_m", 450):
        lines.append(f"🏫 {school['name']} {sd:,}m")
    if households is not None:
        lines.append(f"🏢 배후 {apartment_radius:,}m {households:,}세대")
    expensive = False
    if rent:
        man = rent / 10_000   # dong_rent.json은 원 단위 — 매물 쪽(만원)과 단위를 맞춘다
        if man <= r.get("rent_low_manwon_py", 13.0):
            tags.append("💸 저임대 생활권")
        elif man >= r.get("rent_high_manwon_py", 15.6):
            tags.append("💰 고임대 상업지")
            expensive = True
        line = f"🏘 {dong} 1층 {man:.1f}만/평"
        per_py = listing_rent_per_py(cfg, listing)
        if per_py:
            line += f" · 이 매물 {per_py:.1f}만 ({(per_py / man - 1) * 100:+.0f}%)"
        lines.append(line)
    if station and td is not None:
        if expensive and td <= r.get("station_radius_m", 364):
            tags.append("🚫 위험입지")   # 고임대 상업지 + 역 앞: 3년 생존 37%
        lines.append(f"🚇 {station['name']}역 {td:,}m")
    return tags, lines

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


# 시설 라벨 판정용 신호 (2026-08-23 실측, 알림 282건 기준)
#  - 운영중 표현: 정밀도 79% / 재현율 69% — 가장 강한 단일 신호
#  - 권리금>0     : 정밀도 59%  — 단독으로는 약함 (41%가 빈 상가)
#  - 사진 개수    : 정밀도 29%  — 무의미해서 쓰지 않는다 (빈 상가도 사진을 많이 올림)
RUNNING_RE = re.compile(
    r"운영\s*중|운영해|운영하|운영했|영업\s*중|영업해|"
    r"\d+\s*년\s*(?:째|간|동안|정도|넘게)|매출|단골|폐업"
)
HANDOVER_RE = re.compile(
    r"양도|인수|넘기|넘겨|넘김|시설\s*완비|시설완비|인테리어\s*그대로|그대로\s*사용|"
    r"집기\s*포함|풀\s*옵션|바로\s*영업|즉시\s*영업|기물"
)
NO_PREMIUM_RE = re.compile(r"무권리|권리금\s*없|권리\s*없")


# ── 양도 매물 판정 ─────────────────────────────────────────────────────────────
# 2026-08-23 서울 무인카페 인허가 1,352건 전수 생존분석 결론: 2025년부터 폐업이 2배로 늘어
# "2~3년차에 지친 사람의 양도"가 신규 개업보다 수급상 유리하다. 봇의 1순위는 양도 매물.
#
# 양도 = "누군가 운영하던 카페를 통째로 넘긴다". 판정 경로 셋 중 하나:
#   ① 명시: 카페 단어와 양도 표현이 같은 절 30자 이내 ("카페 양도합니다")
#   ② 장비: 커피머신·제빙기·그라인더 등 카페 장비를 넘긴다는 글
#   ③ 서술: 카페 단어 + 능동 양도 표현 + 운영 흔적 (문장이 끊긴 글용)
# 양도 표현만으로는 안 된다 — 빈 상가 광고도 "권리금 양도" "양도양수 전문"을 쓴다.
#
# 2026-08-24 알림 300건 캘리브레이션: "무인 + 양도"로 잡으면 무인 아이스크림·문구점·계란할인점·
# 미용실·파티룸·만화카페 양도가 절반을 차지했다. → 양도되는 업종이 '카페'여야 한다.
# 카페 프랜차이즈 상호도 카페 단어로 친다 ("더벤티 아현역점 양도합니다"에는 '카페'가 없다)
CAFE_BRANDS = (
    r"더벤티|벤티|메가커피|메가엠지씨|컴포즈|빽다방|이디야|매머드|감성커피|커피에\s*반하다|카페인24|데이롱|"
    r"만월경|프리헷|더리터|에그카페|카페일분|할리스|투썸|파스쿠찌|폴바셋|커피빈|스타벅스|엔제리너스|"
    r"요거프레소|커피스미스|셀렉토|커피마마|드롭탑|탐앤탐스|쥬씨|텐퍼센트|더착한커피|하삼동|"
    r"카페베네|디저트39|빈스빈스|커피베이|토프레소|공차|마노핀"
)
CAFE_WORD = r"(?:카페|까페|커피|cafe|coffee|커피숍|커피전문점|" + CAFE_BRANDS + r")"
CAFE_WORD_RE = re.compile(CAFE_WORD, re.I)
# 카페 문맥에서 "양도합니다" 류 — 문장이 끊겨도 잡기 위한 2차 경로용 (능동 양도 표현만)
HANDOVER_ACTIVE_RE = re.compile(r"양도\s*(?:합니다|해요|해\s*드|하려|하고자|하게|할게|합니당|드립니다|받으실|희망|사유|결정)|넘기|넘겨|넘깁니다|인수하실\s*분|양수하실\s*분")
# 카페 단어와 양도 표현이 같은 절에서 30자 이내 (어느 쪽이 먼저든)
HANDOVER_EXPLICIT_RE = re.compile(
    CAFE_WORD + r"[^\n.!?;]{0,30}?(?:양도|양수|인수|넘기|넘겨|넘김|매각|매도)"
    r"|(?:양도|양수|인수|넘기|넘겨|넘김|매각|매도)[^\n.!?;]{0,30}?" + CAFE_WORD,
    re.I,
)
# 카페 전용 장비 — 이게 있으면 "카페를 넘긴다"는 뜻
CAFE_EQUIPMENT_RE = re.compile(
    r"커피\s*머신|에스프레소|메일빈|동구전자|제빙기|그라인더|원두|카페\s*(?:집기|기물|장비|시설)|커피\s*(?:장비|기계)", re.I
)
# 카페가 아닌 업종이 양도 대상인 글 — 카페 단어가 있어도 탈락
OTHER_BIZ_RE = re.compile(
    r"(?:무인|셀프)?\s*(?:아이스크림|문구|문방구|계란|빨래방|세탁|사진관|셀프\s*사진|프린트|인쇄|밀키트|정육|반찬|과일|"
    r"라면|편의점|마트|슈퍼|펫샵|애견|미용실|헤어|네일|피부|왁싱|마사지|태닝|헬스|필라테스|요가|피트니스|"
    r"파티룸|스터디|독서실|만화|코인노래|노래방|연습실|드럼|피아노|보컬|공방|꽃집|플라워|의류|옷가게|편집숍|"
    r"술집|혼술|바\b|포차|호프|주점|치킨|피자|분식|김밥|떡볶이|도시락|밥집|식당|고깃집|횟집|초밥|"
    r"샤브|국밥|곱창|족발|보쌈|돈까스|중국집|일식|한식|양식|레스토랑|브런치|베이커리|빵집|와플|붕어빵|"
    r"학원|교습소|과외|어린이집|키즈|오락실|PC방|피시방|볼링|당구|탁구|골프|스크린|공인중개|부동산)"
    r"[^\n.!?;]{0,25}?(?:양도|양수|넘기|넘겨|넘김|매각)"
)
# 판정 전에 지워 버리는 문구 — 양도와 무관한데 단어만 겹치는 것들
HANDOVER_NOISE = ["양도세", "양도소득", "양도양수 전문", "양도 양수 전문", "양도양수전문",
                  "양도양수등", "양도양수 등", "양도양수도", "양도양수 추천", "양도양수가능", "양도양수 가능",
                  "양도 불가", "양도불가", "양도 금지", "양도금지", "전대 양도", "전대·양도",
                  "전 분야 매물", "매물 보유"]


def _explicit_handover(body: str) -> bool:
    for clause in CLAUSE_SPLIT_RE.split(body):
        for m in HANDOVER_EXPLICIT_RE.finditer(clause):
            # 추천업종 나열 안의 "카페"는 제외, 양도 뒤 12자 내 부정어도 제외
            back = clause[max(0, m.start() - RECOMMEND_LOOKBACK):m.start()]
            if RECOMMEND_RE.search(back):
                continue
            window = clause[m.end():m.end() + NEGATION_WINDOW]
            if NEGATION_RE.search(window):
                continue
            # "무상 인수 가능하며 카페 등의 용도로 추천" — 빈 상가가 카페를 '추천'하는 문장
            if re.search(r"추천|용도", clause[m.end():m.end() + 15]):
                continue
            return True
    return False


def handover_grade(listing: dict):
    """(우선순위, 라벨). 0이면 양도 매물이 아니다.
    2 = 🔥 무인카페 양도 (우리 업종 그대로) / 1 = 🔥 카페 양도 (시설 인수 후 무인 전환)

    판정: ① "카페 … 양도" 같은 명시 표현, 또는 ② 카페 전용 장비를 넘긴다는 글.
    단, 양도 대상이 다른 업종(아이스크림·미용실·술집…)으로 읽히면 탈락."""
    body = listing.get("content") or ""
    for phrase in HANDOVER_NOISE:
        body = body.replace(phrase, "")

    explicit = _explicit_handover(body)
    equipment = bool(CAFE_EQUIPMENT_RE.search(body) and HANDOVER_RE.search(body))
    # 2차 경로: "6년간 운영한 개인카페입니다. 다른 일을 하게 되어 양도합니다" — 문장이 끊겨
    # 1차(30자 이내)를 못 넘는 글. 카페 단어 + 능동 양도 표현 + 운영 흔적이 전부 있어야 한다.
    narrative = bool(CAFE_WORD_RE.search(body) and HANDOVER_ACTIVE_RE.search(body) and RUNNING_RE.search(body))
    if not (explicit or equipment or narrative):
        return 0, ""
    # 카페 단어가 문장에 있어도 실제 양도 대상이 다른 업종이면 제외
    # (단, 카페 장비를 구체적으로 넘기는 글은 카페로 본다)
    if OTHER_BIZ_RE.search(body) and not CAFE_EQUIPMENT_RE.search(body):
        return 0, ""
    # '○○카페'는 커피집이 아니다 — 양도 대상이 이런 카페면 탈락
    if re.search(r"(?:만화|룸|스터디|키즈|애견|보드|북|PC|피시|게임|코인|셀프사진|사진|파티|방탈출)\s*카페", body, re.I) \
            and not CAFE_EQUIPMENT_RE.search(body):
        return 0, ""
    if re.search(r"무인\s*(?:카페|까페|커피|cafe)|카페\s*무인|무인\s*운영|무인\s*매장|무인\s*시스템", body, re.I):
        return 2, "🔥 무인카페 양도"
    return 1, "🔥 카페 양도"


def facility_tag(listing: dict) -> str:
    """시설이 갖춰져 있는지 표시만 한다 (거르지 않음).
    STRATEGY 전제가 '빈 상가가 아니라 기존 카페 인수'라 이 구분이 핵심이다."""
    body = listing.get("content") or ""
    premium = listing.get("premium_money") or 0
    running = bool(RUNNING_RE.search(body))
    handover = bool(HANDOVER_RE.search(body))

    if running or (premium > 100 and handover):
        return "🏗 시설완비"
    if premium > 0 or handover:
        return "🔧 일부시설"
    if NO_PREMIUM_RE.search(body):
        return "🚧 빈상가"
    return "🚧 빈상가"


def breakeven_cups(cfg: dict, listing: dict):
    """하루 몇 잔을 팔아야 회수기한 안에 본전인지 (필요잔수). 월세가 없으면 None.
    필요잔수 = (월세 + 월 고정비 + (권리금+기준투자)/회수개월) ÷ (잔당 공헌이익 × 30일)
    - 잔당 공헌이익 1,300원 = STRATEGY §12. 월 환산 3.9만/잔
    - 보증금은 반환금이라 넣지 않는다 (STRATEGY §11 — 투자손실 계산과 분리)
    - 권리금은 비회수 투자라 회수기한(§17 상한 18개월)으로 나눠 월 부담에 가산
    검산 앵커: 월세 75/권리금 0 → 39.9잔 (40잔 본전선), 월세 114 → 50.0잔"""
    b = (cfg.get("realty") or {}).get("budget") or {}
    allowed = (cfg.get("realty") or {}).get("trade_types")
    monthly = rent_manwon(listing, allowed)
    if monthly is None:
        return None
    premium = listing.get("premium_money") or 0
    fixed = b.get("fixed_cost_manwon", 25)
    invest = premium + b.get("base_invest_manwon", 1000)
    margin = b.get("cup_margin_won", 1300) * 30 / 10_000   # 만원/잔·월
    return (monthly + fixed + invest / b.get("payback_months", 18)) / margin


def optimal_location(cfg: dict, listing: dict) -> bool:
    """생존분석 최적 프로파일: 저임대 생활권 + 학교 도보권 + 배후 공동주택 세대수.
    학교와 세대수는 단독보다 결합됐을 때 생존율이 높았다.
    좌표가 없으면 False (판정 불가는 최적이 아님)."""
    r = (cfg.get("realty") or {})
    rent, _ = dong_rent(listing)
    if not rent or rent / 10_000 > r.get("rent_low_manwon_py", 13.0):
        return False
    _, sd = nearest_school(listing)
    households = apartment_households(listing, r.get("apartment_radius_m", 600))
    return (sd is not None
            and sd <= r.get("school_radius_m", 450)
            and households is not None
            and households >= r.get("apartment_min_households", 2000))


def passes_location_filter(cfg: dict, listing: dict) -> bool:
    """1차 입지 필터: 고임대 상업지(행정동 상위 1/3)는 제외한다.

    좌표나 행정동 임대료가 없으면 파싱/데이터 누락 때문에 좋은 매물을 잃지 않도록
    통과시킨다. 알려진 값이 기준 이상일 때만 확정 탈락한다.
    """
    r = (cfg.get("realty") or {})
    rent, _ = dong_rent(listing)
    if rent is None:
        return True
    return rent / 10_000 < r.get("rent_high_manwon_py", 15.6)


def passes_budget_filter(cfg: dict, listing: dict) -> bool:
    """2차 손익 필터: 목표 상단(기본 50잔/일) 안에서 본전이 나는 매물만 통과.

    월세를 파싱하지 못한 매물은 확인 기회를 남기기 위해 통과시키고 ⚪로 표시한다.
    """
    b = (cfg.get("realty") or {}).get("budget") or {}
    if not b:
        return True
    cups = breakeven_cups(cfg, listing)
    return cups is None or cups <= b.get("review_cups", 50)


def budget_tag(cfg: dict, listing: dict) -> str:
    """예산 라벨. 실제 알림은 passes_budget_filter를 통과한 뒤 이 라벨을 표시한다.
    💚는 예산과 입지가 모두 최적 프로파일일 때만 붙는 종합 판정 — 드물게 뜨는 게 정상.
    🟡/🔴 경계는 필요잔수 기준 (50잔 = 목표 상단, 권리금 0이면 월세 ~114만 상당)."""
    b = (cfg.get("realty") or {}).get("budget") or {}
    if not b:
        return ""
    cups = breakeven_cups(cfg, listing)
    if cups is None:
        return "⚪ 금액미상"
    allowed = (cfg.get("realty") or {}).get("trade_types")
    deposit = deposit_manwon(listing, allowed) or 0
    premium = listing.get("premium_money") or 0
    if (cups <= b.get("green_cups", 40)
            and deposit <= b.get("green_deposit", 2000)
            and premium <= b.get("green_premium", 300)      # §11 권리금 0~300 상한 유지
            and optimal_location(cfg, listing)):
        return "💚 무인카페 최적"
    if cups <= b.get("review_cups", 50):
        return "🟡 검토"
    return "🔴 예산초과"


# 관리비가 월세에 안 들어 있다는 신호 — 금액을 모르니 필요잔수에 +α로만 표시.
# "관리비 없음/포함"은 매칭하지 않게 별도·숫자만 잡는다 (정밀도 우선).
MAINT_EXTRA_RE = re.compile(r"관리비\s*(?:별도|\d)")


def budget_line(cfg: dict, listing: dict) -> str:
    """'☕ 본전 ~N잔/일' 표시 줄. 등급과 같은 값을 정수 올림으로 보여준다.
    정상 알림은 50잔 이하만 통과하지만 수동 분석에서도 같은 계산값을 쓴다."""
    cups = breakeven_cups(cfg, listing)
    if cups is None:
        return ""
    extra = "+α" if MAINT_EXTRA_RE.search(listing.get("content") or "") else ""
    return f"☕ 본전 ~{math.ceil(cups)}잔/일{extra}"


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
    입지→손익 2단 필터와 키워드 규칙을 모두 통과하면 규칙 이름, 아니면 None."""
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

    # 1차: 무인카페 생존율이 낮은 고임대 상업지(행정동 상위 1/3) 제외
    if not passes_location_filter(cfg, listing):
        return None

    # 2차: 통과한 생활권 안에서도 목표 상단(50잔/일)을 넘는 매물 제외
    if not passes_budget_filter(cfg, listing):
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
    # 제외어가 있으면 그 부분을 지우고 판정한다 ("만화카페 폐업" 같은 다른 업종 걸러내기)
    for phrase in search_cfg.get("exclude_keywords") or []:
        text = text.replace(phrase, "")
    # require_any: 이 중 하나는 본문에 있어야 통과 — "카페 양도" 검색이 아이돌 생일카페
    # 굿즈 양도(특전·포카·럭드)에 도배되는 문제(2026-08-24). 진짜 점포 양도 글에는
    # 권리금·보증금·머신·집기 같은 단어가 반드시 나온다.
    req = search_cfg.get("require_any") or []
    if req and not any(k in text for k in req):
        return "perm"   # 조건 미달은 영구 탈락 — 같은 굿즈 글이 계속 재평가되지 않게
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

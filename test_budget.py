"""예산 라벨(필요잔수) 회귀 테스트. budget_tag·breakeven_cups·budget_line·
listing_rent_per_py를 건드리면 여기부터 통과시킬 것.

앵커는 STRATEGY §12(잔당 공헌이익 1,300원)·§13(회수 상한)에서 유도된 값이다:
필요잔수 = (월세 + 25 + (권리금+1000)/18) ÷ 3.9 — 월세 75 → 39.9잔, 월세 114 → 50.0잔.
"""
import sys

import matching
import yaml


def cfg():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def listing(monthly=None, deposit=0, premium=0, lat=None, lon=None,
            floor=None, area=None, content=""):
    L = {"premium_money": premium, "lat": lat, "lon": lon,
         "floor": floor, "area_m2": area, "content": content, "trades": []}
    if monthly is not None:
        L["trades"] = [{"type": "MONTH", "monthlyPay": monthly, "deposit": deposit}]
    return L


# 싼 동(성현동 11.0만/평) + 초중고 0m — 입지 최적 프로파일 기준점 (구암중학교 좌표)
OPT = {"lat": 37.49268, "lon": 126.94815}
# 비싼 동(역삼1동) — 입지 최적이 아님 (test_location과 같은 기준점)
EXP = {"lat": 37.50062, "lon": 127.03641}


def run():
    c = cfg()
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: 기대 {want!r} / 실제 {got!r}")

    # ── 필요잔수 공식 앵커 ──
    n = matching.breakeven_cups(c, listing(75, 1000))
    if not (39.7 <= n <= 40.1):
        fails.append(f"필요잔수(75/1000): 기대 39.9±0.2 / 실제 {n:.2f}")
    n = matching.breakeven_cups(c, listing(114, 2000))
    if not (49.7 <= n <= 50.1):
        fails.append(f"필요잔수(114): 기대 50.0±0.2 / 실제 {n:.2f}")
    # 보증금은 잔수에 안 들어간다 (반환금 — STRATEGY §11)
    if matching.breakeven_cups(c, listing(75, 1000)) != matching.breakeven_cups(c, listing(75, 9000)):
        fails.append("보증금이 필요잔수를 바꿈 — 반환금은 잔수에서 제외해야 함")
    # 권리금은 회수개월로 나눠 가산된다
    if not matching.breakeven_cups(c, listing(50, 0, premium=200)) > matching.breakeven_cups(c, listing(50, 0)):
        fails.append("권리금이 필요잔수에 가산되지 않음")

    # ── 등급 ──
    check("월세 75 (입지 미상)", matching.budget_tag(c, listing(75, 1000)), "🟡 검토")
    check("월세 120", matching.budget_tag(c, listing(120, 2000)), "🔴 예산초과")
    check("금액 없음", matching.budget_tag(c, listing()), "⚪ 금액미상")
    # 💚 = 예산 + 입지(싼동네+학교) 모두 최적
    check("최적 입지+예산", matching.budget_tag(c, listing(60, 1000, premium=200, **OPT)), "💚 무인카페 최적")
    check("최적 입지, 권리금 초과", matching.budget_tag(c, listing(30, 500, premium=400, **OPT)), "🟡 검토")
    check("최적 입지, 보증금 초과", matching.budget_tag(c, listing(60, 3000, **OPT)), "🟡 검토")
    check("비싼 동, 예산은 통과", matching.budget_tag(c, listing(60, 1000, **EXP)), "🟡 검토")

    # ── 표시 줄 ──
    check("본전 줄", matching.budget_line(c, listing(75, 1000)), "☕ 본전 ~40잔/일")
    check("관리비 별도 +α", matching.budget_line(c, listing(75, 1000, content="관리비 별도")),
          "☕ 본전 ~40잔/일+α")
    check("관리비 없음", matching.budget_line(c, listing(75, 1000, content="관리비 없음")),
          "☕ 본전 ~40잔/일")
    check("금액 없음 줄 생략", matching.budget_line(c, listing()), "")

    # ── 평당 시세 비교 (동 평균은 원 단위, 매물은 만원 단위 — 단위 변환 회귀) ──
    L = listing(75, 1000, floor="1.0", area="20", **OPT)   # 20㎡=6.05평 → (75+10)/6.05=14.0만
    py = matching.listing_rent_per_py(c, L)
    if py is None or not (13.9 <= py <= 14.2):
        fails.append(f"평당 환산: 기대 14.0±0.1 / 실제 {py}")
    check("반지하 제외", matching.listing_rent_per_py(c, listing(75, 1000, floor="0.5", area="20")), None)
    check("층 미상 제외", matching.listing_rent_per_py(c, listing(75, 1000, area="20")), None)
    check("면적 이상치 제외", matching.listing_rent_per_py(c, listing(75, 1000, floor="1.0", area="132.23")), None)
    _, lines = matching.location_tags(c, L)
    merged = next((x for x in lines if x.startswith("🏘")), "")
    if "이 매물 14.0만" not in merged or "%" not in merged:
        fails.append(f"🏘 시세 비교 줄: '이 매물 14.0만 (±%)' 기대 / 실제 {merged!r}")
    return fails


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = run()
    if f:
        print("FAIL")
        for x in f:
            print("  -", x)
        sys.exit(1)
    print("OK — 필요잔수 앵커, 등급 게이트, 표시 줄, 평당 시세 비교 통과")

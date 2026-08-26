"""입지 라벨(학교·배후세대·동 임대료·역) 회귀 테스트. 데이터 파일이 빠지거나 좌표 판정이 깨지면 잡힌다.

기준점은 2026-08-24에 카카오맵·인허가 좌표로 검증한 실제 값들이다.
"""
import sys

import matching
import yaml


def cfg():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


CASES = [
    # (이름, lat, lon, 행정동, 임대료범위, 역, 역거리, 학교450m?, 공동주택600m 세대수범위)
    ("관악 참숯길 3",       37.47944, 126.94258, "청룡동", (12.0, 15.5), "봉천", (250, 450), True, (3000, 4500)),
    ("동대문 전농로38길 87", 37.58781, 127.05599, "휘경2동", (10.0, 20.0), "회기", (100, 400), True, (2000, 3000)),
    ("강남 역삼역 앞",       37.50062, 127.03641, "역삼1동", (18.0, 30.0), "역삼", (0, 200), None, (500, 1000)),
]


def run():
    c = cfg()
    fails = []
    if (not matching.SCHOOLS or not matching.APARTMENTS or not matching.SUBWAY
            or not matching.DONG_RENT or not matching.DONG_BOUNDARY):
        fails.append("입지 데이터 파일(schools/apartments/subway/dong_rent/dong_boundary.json) 중 하나가 비어 있음")
        return fails
    for name, lat, lon, dong, (lo, hi), station, (dmin, dmax), school, (hlo, hhi) in CASES:
        L = {"lat": lat, "lon": lon}
        d = matching.dong_of(L)
        if not d or d["name"] != dong:
            fails.append(f"{name}: 행정동 기대 {dong} / 실제 {d and d['name']}")
        rent, _ = matching.dong_rent(L)
        if rent is None or not (lo * 10_000 <= rent <= hi * 10_000):
            fails.append(f"{name}: 임대료 기대 {lo}~{hi}만 / 실제 {rent}")
        st, td = matching.nearest_station(L)
        if station and (not st or not st["name"].startswith(station) or not (dmin <= td <= dmax)):
            fails.append(f"{name}: 역 기대 {station} {dmin}~{dmax}m / 실제 {st and st['name']} {td}")
        sc, sd = matching.nearest_school(L)
        if school is not None and ((sd <= 450) != school):
            fails.append(f"{name}: 학교 450m 안 기대 {school} / 실제 {sc and sc['name']} {sd}m")
        households = matching.apartment_households(L, 600)
        if households is None or not (hlo <= households <= hhi):
            fails.append(f"{name}: 배후세대 기대 {hlo}~{hhi} / 실제 {households}")
    # 학교와 2,000세대가 함께 있는 두 기준점만 결합 라벨
    for name, lat, lon, *rest in CASES[:2]:
        tags, lines = matching.location_tags(c, {"lat": lat, "lon": lon})
        if "🏘️ 대단지학교권" not in tags or not any(x.startswith("🏢 배후 600m") for x in lines):
            fails.append(f"{name}: 대단지학교권/배후세대 줄 기대 / 실제 {tags} {lines}")
    # 라벨 조합과 1차 실제 필터: 고임대 상업지+역 앞 → 🚫/탈락, 저임대 생활권 → 통과
    tags, _ = matching.location_tags(c, {"lat": 37.50062, "lon": 127.03641})
    if "🚫 위험입지" not in tags or "💰 고임대 상업지" not in tags:
        fails.append(f"역삼역 앞: 🚫/고임대 상업지 기대 / 실제 {tags}")
    if matching.passes_location_filter(c, {"lat": 37.50062, "lon": 127.03641}):
        fails.append("고임대 상업지가 1차 입지 필터를 통과함")
    low = {"lat": 37.48893, "lon": 126.95715}   # 저임대 기준점 (1층 11.0만/평)
    tags, _ = matching.location_tags(c, low)
    if "💸 저임대 생활권" not in tags or "🚫 위험입지" in tags:
        fails.append(f"저임대 생활권: 💸 기대 / 실제 {tags}")
    if not matching.passes_location_filter(c, low):
        fails.append("저임대 생활권이 1차 입지 필터에서 탈락함")
    # 좌표 없는 매물은 조용히 빈 결과
    unknown = {"lat": None, "lon": None}
    tags, lines = matching.location_tags(c, unknown)
    if tags or lines:
        fails.append("좌표 없는 매물에 입지 라벨이 붙음")
    if not matching.passes_location_filter(c, unknown):
        fails.append("좌표 없는 판정불가 매물이 1차 필터에서 탈락함")
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
    print(f"OK — 입지 기준점 {len(CASES)}건, 라벨 조합, 좌표 없음 처리 통과")

"""입지 라벨(학교·동 임대료·역) 회귀 테스트. 데이터 파일이 빠지거나 점-폴리곤이 깨지면 여기서 잡힌다.

기준점은 2026-08-24에 카카오맵·인허가 좌표로 검증한 실제 값들이다.
"""
import sys

import matching
import yaml


def cfg():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


CASES = [
    # (이름, lat, lon, 기대 행정동, 기대 임대료 범위(만/평), 기대 역, 역거리 범위 m, 학교 450m 안?)
    ("관악 참숯길 3",       37.47944, 126.94258, "청룡동", (12.0, 15.5), "봉천", (250, 450), True),
    ("동대문 전농로38길 87", 37.58781, 127.05599, "휘경2동", (10.0, 20.0), "회기", (100, 400), True),
    ("강남 역삼역 앞",       37.50062, 127.03641, "역삼1동", (18.0, 30.0), "역삼", (0, 200), None),
]


def run():
    c = cfg()
    fails = []
    if not matching.SCHOOLS or not matching.SUBWAY or not matching.DONG_RENT or not matching.DONG_BOUNDARY:
        fails.append("입지 데이터 파일(schools/subway/dong_rent/dong_boundary.json) 중 하나가 비어 있음")
        return fails
    for name, lat, lon, dong, (lo, hi), station, (dmin, dmax), school in CASES:
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
    # 라벨 조합: 비싼 동 + 역 앞 → 🚫, 싼 동 → 💸
    tags, _ = matching.location_tags(c, {"lat": 37.50062, "lon": 127.03641})
    if "🚫 위험입지" not in tags or "💰 비싼동네" not in tags:
        fails.append(f"역삼역 앞: 🚫/💰 기대 / 실제 {tags}")
    tags, _ = matching.location_tags(c, {"lat": 37.48893, "lon": 126.95715})   # 관악 성현동 (1층 11.0만/평)
    if "💸 싼동네" not in tags or "🚫 위험입지" in tags:
        fails.append(f"성현동: 💸 기대 / 실제 {tags}")
    # 좌표 없는 매물은 조용히 빈 결과
    tags, lines = matching.location_tags(c, {"lat": None, "lon": None})
    if tags or lines:
        fails.append("좌표 없는 매물에 입지 라벨이 붙음")
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

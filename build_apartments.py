"""서울 공동주택 원자료 CSV를 입지 판정용 최소 JSON으로 변환한다.

원자료: 서울 열린데이터광장 공동주택 정보(Open API, OA-15818)
https://data.seoul.go.kr/dataList/OA-15818/S/1/datasetView.do

사용법:
    python build_apartments.py 원자료.csv [apartments.json]
"""
import csv
import json
import sys
from pathlib import Path


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build(source: Path) -> list[dict]:
    apartments = []
    with source.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("원본_USE_YN") or "Y").strip().upper() == "N":
                continue
            lat = _number(row.get("위도"))
            lon = _number(row.get("경도"))
            households = _number(row.get("세대수"))
            year = _number(row.get("건축연도"))
            if lat is None or lon is None or households is None or households <= 0:
                continue
            if not (37.4 <= lat <= 37.72 and 126.75 <= lon <= 127.2):
                continue
            item = {
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "hh": int(households),
            }
            if year:
                item["year"] = int(year)
            apartments.append(item)
    apartments.sort(key=lambda x: (x["lat"], x["lon"], x["hh"]))
    return apartments


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("사용법: python build_apartments.py 원자료.csv [apartments.json]")
    source = Path(sys.argv[1])
    output = Path(sys.argv[2]) if len(sys.argv) == 3 else Path(__file__).with_name("apartments.json")
    apartments = build(source)
    output.write_text(
        json.dumps(apartments, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"{output}: {len(apartments):,}개 단지")


if __name__ == "__main__":
    main()

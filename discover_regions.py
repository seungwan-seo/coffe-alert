"""서울 전체 동(洞) id 목록을 한 번 수집해 seoul_regions.json으로 저장한다.

당근 중고거래 검색의 in= 파라미터는 동 id만 받으므로, 서울 커버리지는 이 목록 순회로 만든다.
동작: 알려진 id(종로 11)에서 시작해, 응답의 siblingRegions(같은 구의 전체 동)를 흡수하고
다음 id로 점프하는 방식으로 서울 25개 구를 훑는다.

사용법: python discover_regions.py
"""
import json
import os
import sys
import time

from daangn_buysell import Blocked, region_meta

OUT = os.path.join(os.path.dirname(__file__), "seoul_regions.json")
DELAY = 0.6
MAX_REQUESTS = 250
ID_LIMIT = 1200
# 서울 행정동 id는 저번호 대역(실측: 종로 3~, 관악 341~355)에 몰려 있고,
# siblingRegions에는 6000번대 법정동 id도 섞여 온다. 순회는 행정동만으로 충분하다.
LOW_BLOCK = 2000


def main() -> None:
    dongs = {}      # id -> {id, gu, dong}
    gus = set()
    cursor = 3      # 실측: 서울 행정동 id 시작점(청운효자동)
    requests_used = 0

    while cursor <= ID_LIMIT and requests_used < MAX_REQUESTS and len(gus) < 25:
        try:
            meta = region_meta(cursor)
        except Blocked as e:
            print(f"차단됨: {e} — 지금까지 수집분만 저장합니다.")
            break
        requests_used += 1
        time.sleep(DELAY)

        if meta["resolved_id"] != cursor or meta["sido"] != "서울특별시":
            cursor += 1
            continue

        siblings = [
            s for s in meta["siblings"]
            if s["sido"] == "서울특별시" and s["id"] < LOW_BLOCK
        ]
        if not siblings:
            siblings = [{"id": cursor, "sido": meta["sido"], "gu": meta["gu"], "dong": meta["dong"]}]
        for s in siblings:
            dongs[s["id"]] = {"id": s["id"], "gu": s["gu"], "dong": s["dong"]}
            gus.add(s["gu"])
        print(f"{meta['gu']}: 동 {len(siblings)}개 (누적 {len(dongs)}동 / {len(gus)}구, 요청 {requests_used}회)")
        cursor = max(s["id"] for s in siblings) + 1

    if not dongs:
        sys.exit("수집 실패 — 아무 지역도 얻지 못했습니다.")

    result = sorted(dongs.values(), key=lambda d: d["id"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n저장: {OUT} — {len(gus)}개 구, {len(result)}개 동")
    missing = 25 - len(gus)
    if missing:
        print(f"경고: {missing}개 구를 찾지 못했습니다. ID_LIMIT/MAX_REQUESTS를 늘려 재실행하세요.")


if __name__ == "__main__":
    main()

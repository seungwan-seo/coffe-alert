"""번개장터 중고 장비 검색.

공개 JSON API (2026-08 실측, 인증·쿠키 불필요):
  GET api.bunjang.co.kr/api/1/find_v2.json?q={키워드}&order=date&page=0&n={개수}
  → {"num_found": N, "list": [{pid, name, price, location, update_time, status, ...}]}

- order=date로 최신순 정렬됨 (실측 확인)
- status "0" = 판매중
- 지역 파라미터(f_location)는 무시되므로 location 문자열로 클라이언트 필터
- location이 빈 문자열인 매물이 절반 가까이 있음 (지역 미표기) — 설정으로 포함/제외 선택
"""
import time

import requests

API = "https://api.bunjang.co.kr/api/1/find_v2.json"
WEB = "https://m.bunjang.co.kr/products/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class Blocked(Exception):
    pass


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def search(keyword: str, limit: int = 100) -> list:
    resp = SESSION.get(
        API,
        params={"q": keyword, "order": "date", "page": 0, "n": limit,
                "req_ref": "search", "stat_device": "w"},
        timeout=15,
    )
    if resp.status_code in (403, 429):
        raise Blocked(f"{resp.status_code} on bunjang '{keyword}'")
    resp.raise_for_status()
    data = resp.json()
    if "list" not in data:
        raise Blocked(f"empty structure on bunjang '{keyword}'")

    items = []
    for a in data["list"]:
        if a.get("ad"):          # 광고 슬롯 제외
            continue
        try:
            price = int(float(a["price"])) if a.get("price") else None
        except (TypeError, ValueError):
            price = None
        pid = str(a.get("pid") or "")
        if not pid:
            continue
        items.append({
            "id": f"bj{pid}",     # 다른 소스와 ID 충돌 방지
            "url": WEB + pid,
            "title": a.get("name") or "",
            "content": "",        # 목록에는 본문이 없음
            "price": price,
            "thumbnail": (a.get("product_image") or "").replace("{res}", "300"),
            "status": "Ongoing" if str(a.get("status")) == "0" else "Closed",
            "region": a.get("location") or "",
            "created_at": _iso(a.get("update_time")),
        })
    return items


def _iso(unix_ts) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(unix_ts)))
    except (TypeError, ValueError):
        return ""


def polite_sleep(delay: float) -> None:
    time.sleep(delay)

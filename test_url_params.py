"""매물 URL 파라미터 회귀 테스트.

2026-08-22: af(지도 필터)가 없어서 지도가 아파트 매물만 보여주고 정작 해당 상가는
안 찍히는 문제가 있었다. 같은 실수를 다시 하지 않도록 고정한다.

실행: python test_url_params.py   (네트워크 불필요)
"""
import sys
from urllib.parse import parse_qs, unquote, urlparse

from daangn_realty import article_url

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  OK  {msg}")
    else:
        print(f"  !!  {msg}")
        FAILED.append(msg)


print("1) 좌표+종류+거래유형이 모두 있을 때")
url = article_url("3532670", "37.5538625", "127.1238341", "STORE", "MONTH")
q = parse_qs(urlparse(url).query)
check("af" in q, "af(지도 필터) 파라미터가 있다 — 없으면 지도가 아파트를 보여준다")
check("mv" in q, "mv(뷰포트) 파라미터가 있다")
af = unquote(q.get("af", [""])[0])
check('"salesTypes":["STORE"]' in af, f"af에 매물종류가 담긴다: {af}")
check('"tradeTypes":["MONTH"]' in af, "af에 거래유형이 담긴다")
mv = unquote(q.get("mv", [""])[0]).split(",")
check(len(mv) == 5 and mv[4] == "17", f"mv는 좌표4개+줌 형식이다: {mv}")
check(float(mv[0]) < 37.5538625 < float(mv[2]), "위도가 뷰포트 안에 들어온다")
check(float(mv[1]) < 127.1238341 < float(mv[3]), "경도가 뷰포트 안에 들어온다")

print("\n2) 좌표가 없을 때 (폴백)")
url2 = article_url("123", None, None, "STORE", "MONTH")
q2 = parse_qs(urlparse(url2).query)
check("af" in q2, "좌표가 없어도 af는 붙는다")
check("mv" not in q2, "좌표가 없으면 mv는 생략한다")

print("\n3) 아무 정보도 없을 때")
url3 = article_url("123")
check(url3.endswith("/articles/123"), f"파라미터 없이 순수 URL: {url3}")

print("\n4) 잘못된 좌표는 조용히 무시한다")
url4 = article_url("123", "없음", "없음", "STORE", "MONTH")
check("mv=" not in url4, "좌표 파싱 실패 시 mv 생략(예외 없이)")

if FAILED:
    print(f"\n실패 {len(FAILED)}건")
    sys.exit(1)
print("\n전체 통과")

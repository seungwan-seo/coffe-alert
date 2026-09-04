"""양도 매물 판정·우선순위 회귀 테스트. CI에서 크롤러 실행 전에 돈다.

2026-08-24 알림 300건 캘리브레이션에서 실제로 본 문장들로 만들었다.
판정을 손보면 여기부터 통과시킬 것.
"""
import sys

import matching


def L(content, premium=0):
    return {"content": content, "premium_money": premium}


# (본문, 기대 등급) — 등급 2 = 무인카페 양도, 1 = 카페 양도, 0 = 양도 아님
CASES = [
    # ── 잡아야 하는 것 ──
    ("안암동 벽산아파트 상가 내 위치한 무인카페 '온전한,'을 개인 사정으로 양도합니다. 고대생 단골 확보", 2),
    ("그대로 카페 인수가 제일 좋겠지만 다른 업종도 문의 주세요. <포함내역> 커피머신 set(컵 디스펜서+제빙기+커피머신). 무인 운영 중", 2),
    ("송파구 커피&디저트카페 양도합니다:) 현재 영업중이고 4년동안 운영해 온 카페입니다.", 1),
    ("프랜차이즈 테이크아웃 카페 양도합니다. [급매/기물값만 받습니다] 권리금 2,500 파격 인하", 1),
    ("아현역 1-2분 거리에 위치한 더벤티 아현역점 양도합니다. 안정적 매출", 1),          # 브랜드명만
    ("6년간 안정적으로 운영하고있는 개인카페입니다. 다른 일을 하게되어서 양도하게 되었습니다! 단골확보", 1),  # 문장 끊김
    ("카페창업 해보기엔 정말 좋은 조건입니다 (평일엔 고정단골 다수확보) 집기,소품등 다 양도해드리니 바로 영업 가능합니다!", 1),
    ("올 수리 인테리어 약 2년 된 카페입니다. <기기 목록> 씨메 커피머신, 그라인더 2개, 제빙기. 그대로 인수하실 분", 1),
    ("카페 디저트 집기 양도로 인한 권리금으로 그대로 몸만 들어와 카페 하실 분 추천", 1),
    # ── 잡으면 안 되는 것 (2026-08-24 실제 오탐) ──
    ("무인 문구점 양도합니다. 문구방구 서초내곡점. 애정으로 운영해온 가게, 좋은 분께 양도 희망", 0),
    ("3년차 무인으로 운영해오던 좋은계란할인점을 양도합니다.", 0),
    ("동네에서 6년 동안 운영해 온 무인 아이스크림 가게를 권리금 없이 통째로 양도합니다.", 0),
    ("한신한진APT 단지내상가, 무인 아이스크림 매장 양도. 월매출 4백", 0),
    ("건대입구역 3분거리 2층 미용실 양도합니다. 1층에 띵똥와플이 있어서 카페 손님도 많아요", 0),
    ("개인 사정으로 성업 중인 혼술바를 양도합니다. 감성 인테리어와 바 집기, 테이블, 의자", 0),
    ("홍대 1위권 브라이덜샤워 파티룸 양도합니다 (즉시 수익화)", 0),
    ("인근 만화카페가 없어져서 단골이 있는 만화카페입니다. 노인부부가 은퇴하려고 양도하는것이며 인수받아", 0),
    ("드럼, 피아노룸 운영중. 악기까지 양도양수. 레슨, 무인운영 가능", 0),
    ("프랜차이즈, 소자본, 대형카페, 신규 창업, 무인 업종, 고수익 점포 양도양수등 전 분야 매물 보유중이며 상담 가능", 0),  # 중개사 광고
    ("추천업종 : 무인아이스크림, 무인카페, 무인 빨래방, 닭강정. 시설 상태가 좋아 바로 영업 가능합니다. 양도", 0),  # 추천업종 나열
    ("다양한 업종 가능, 양도양수도 OK. 망원역 도보 3분 카페거리 인접. 권리금 상담", 0),
    ("천정 레일등, 벽체 장식등은 무상인수가능하며 카페 등의 용도로 추천합니다", 0),
    ("양도세 부담 없는 매물. 카페 자리로 좋습니다. 권리금 없음", 0),
    ("음식점, 카페 업종은 양도 불가합니다", 0),
    ("", 0),
]


def test_grades():
    bad = []
    for body, want in CASES:
        got, _ = matching.handover_grade(L(body))
        if got != want:
            bad.append((want, got, body[:60]))
    return bad


def test_priority_order():
    """🔥가 앞에 오고, 같은 우선순위끼리는 순서를 유지해야 한다 (main.alert_priority 기준 정렬)."""
    import main
    alerts = [
        {"text": "a", "priority": 0}, {"text": "b", "priority": 2}, {"text": "c", "priority": 0},
        {"text": "d", "priority": 1}, "plain-string",
    ]
    alerts.sort(key=main.alert_priority, reverse=True)
    order = [a["text"] if isinstance(a, dict) else a for a in alerts]
    return [] if order == ["b", "d", "a", "c", "plain-string"] else [("order", order)]


def test_goods_flood():
    """2026-08-24 실제 오탐: 번개 '카페 양도' 검색이 아이돌 생일카페 굿즈 양도로 도배.
    require_any(권리금·보증금·머신·집기 등)가 이를 막는지 — 실제 신고된 제목들로 고정."""
    import yaml
    from datetime import datetime, timezone
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    fails = []
    for section in ("buysell", "bunjang"):
        # "카페 폐업" 검색(별칭 "카페 정리")도 굿즈 "정리" 글을 통과시키면 안 된다 (2026-08-24 2차 구멍)
        pb = next(x for x in cfg[section]["searches"] if x["keyword"] == "카페 폐업")
        for t2, want2 in [
            ("아이브 생일카페 특전 굿즈 정리합니다", "perm"),
            ("세븐틴 생카 컵홀더 카페 굿즈 일괄 정리", "perm"),
            ("카페 폐업 정리 - 제빙기 커피머신 테이블 일괄", "match"),
        ]:
            v2 = matching.match_buysell(pb, {"status": "Ongoing", "created_at": now,
                                             "price": 10000, "title": t2, "content": ""}, 3)
            if v2 != want2:
                fails.append(f"{section}/카페 폐업: '{t2[:24]}' 기대 {want2} 실제 {v2}")
        # "폐업 일괄"은 모든 업종의 재고가 검색된다. 카페·주방 장비 신호가 없는
        # 굿즈/의류/운동시설 폐업 재고는 막고, 실제 업소 장비만 남겨야 한다.
        bulk = next((x for x in cfg[section]["searches"] if x["keyword"] == "폐업 일괄"), None)
        if bulk:
            for title, content, want in [
                ("키링 인형 349개 일괄 (개당2500)", "모두 새상품이고 폐업정리라 일괄로만 판매합니다", "perm"),
                ("키링 인형 349개 일괄 (개당2500)", "카페 폐업으로 재고와 커피머신도 일괄 정리합니다", "perm"),
                ("여성 의류 200벌 일괄", "옷가게 폐업 정리, 행거 포함", "perm"),
                ("[도장폐업] 타타미 매트 + 운동기구 전체 일괄", "폐업 일괄 판매", "perm"),
                ("귀여운 과일 모양 단추 세트 (새제품)", "매장 폐업 정리로 진열대와 함께 판매합니다", "perm"),
                ("다양한 사과 단추 세트", "폐업 일괄, 매장 집기와 함께 정리합니다", "perm"),
                ("귀여운 고양이 4구 키캡키링 2종 -개별가격(새제품)", "폐업 일괄 판매", "perm"),
                ("반짝이는 향수병 키링 (N5, No.1) 개별가격", "매장 폐업 정리", "perm"),
                ("진주 큐빅 헤어집게핀 &자동핀 2종 일괄 새제품", "폐업 일괄, 진열대 포함", "perm"),
                ("큐빅 포인트 헤어 집게핀 2개 세트 (로즈/화이트)", "매장 폐업 정리", "perm"),
                ("반짝이는 별 모양 큐빅 브로치 3개 세트", "폐업 일괄, 진열대 포함", "perm"),
                ("카페 폐업 집기 일괄", "커피머신, 제빙기, 테이블 포함", "match"),
                ("식당 폐업 일괄 정리", "업소용 냉장고, 싱크대, 작업대 포함", "match"),
            ]:
                verdict = matching.match_buysell(
                    bulk,
                    {"status": "Ongoing", "created_at": now, "price": 10000,
                     "title": title, "content": content},
                    3,
                )
                if verdict != want:
                    fails.append(f"{section}/폐업 일괄: '{title[:24]}' 기대 {want} 실제 {verdict}")
        scs = [x for x in cfg[section]["searches"] if x.get("handover")]
        if not any(x.get("require_any") for x in scs):
            fails.append(f"{section}: handover 검색에 require_any 없음")
        for sc in scs:
            for t, want in [
                ("보이넥스트도어 생일 카페 생카 특전 럭드 일괄 양도", "perm"),
                ("하츠투하츠 태국 카페 포카 양도 분철", "perm"),
                ("전독시 연남 굿즈모먼트 콜라보 카페 엽서 양도", "perm"),
                ("플레이브 십카페 럭드 양도", "perm"),
                ("무인카페 양도합니다. 메일빈 머신 포함, 보증금 1000/월세 60", "match"),
                ("운영중인 카페 양도. 시설 집기 일체 포함, 매출 자료 공개", "match"),
            ]:
                v = matching.match_buysell(sc, {"status": "Ongoing", "created_at": now,
                                                "price": 50000, "title": t, "content": ""}, 3)
                if v != want:
                    fails.append(f"{section}/{sc['keyword']}: '{t[:24]}' 기대 {want} 실제 {v}")
    return fails


def test_buysell_priority():
    import main
    hits = []
    if main.handover_priority({"keyword": "제빙기"}, {"title": "무인카페 양도", "content": ""}) != 0:
        hits.append("handover 플래그 없는 검색어가 우선순위를 받음")
    if main.handover_priority({"keyword": "카페 양도", "handover": True}, {"title": "카페 양도합니다", "content": ""}) != 1:
        hits.append("카페 양도 → 1이어야 함")
    if main.handover_priority({"keyword": "카페 양도", "handover": True}, {"title": "무인카페 양도", "content": ""}) != 2:
        hits.append("무인카페 양도 → 2여야 함")
    return hits


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures = []
    for want, got, body in test_grades():
        failures.append(f"등급 기대 {want} / 실제 {got}: {body}")
    failures += [str(x) for x in test_priority_order()]
    failures += test_buysell_priority()
    failures += test_goods_flood()
    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print(f"OK — 판정 {len(CASES)}건, 정렬, 중고거래 우선순위 통과")

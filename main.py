"""당근 부동산/중고거래 신규 매물 → 텔레그램 알림.

사용법:
  python main.py            # 1회 실행 (GitHub Actions cron이 이걸 호출)
  python main.py --dry-run  # 텔레그램 전송 없이 콘솔 출력만 (토큰 없으면 자동 드라이런)

첫 실행은 현재 매물을 전부 '본 것'으로 기록만 하고 알림은 보내지 않는다.
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
import yaml

import commands
import daangn_buysell
import daangn_realty
import matching
import notifier
from state import is_seen, load_state, mark_seen, save_state

HERE = os.path.dirname(__file__)
KST = timezone(timedelta(hours=9))


def add_alert(state: dict, alerts: list, channel: str, text: str, photo=None) -> None:
    alerts.append({"text": text, "photo": photo})
    stats = state.setdefault("stats", {})
    stats[channel] = stats.get(channel, 0) + 1


def load_config() -> dict:
    with open(os.path.join(HERE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_regions() -> list:
    path = os.path.join(HERE, "seoul_regions.json")
    if not os.path.exists(path):
        print("경고: seoul_regions.json 없음 — 중고거래 채널은 신림동(355)만 검색합니다.")
        return [{"id": 355, "gu": "관악구", "dong": "신림동"}]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def realty_alert_text(listing: dict, rule: str) -> str:
    e = notifier.esc
    parts = [f"🏠 <b>[{e(listing['category'])} · {e(daangn_realty.fmt_trade(listing['trades']))}]</b> {e(listing['region'])}"]
    snippet = (listing["content"] or "").strip().replace("\n", " ")
    if len(snippet) > 90:
        snippet = snippet[:90] + "…"
    if snippet:
        parts.append(e(snippet))
    extras = []
    if listing.get("premium_money"):
        extras.append(f"권리금 {daangn_realty.fmt_manwon_full(listing['premium_money'])}")
    if listing.get("area_m2"):
        try:
            extras.append(f"{float(listing['area_m2']) / 3.3058:.1f}평")
        except ValueError:
            pass
    extras.append("중개사" if listing.get("broker") else "직거래")
    parts.append(e(" · ".join(extras)))
    parts.append(f'{listing["url"]}')
    parts.append(f"<i>매칭: {e(rule)}</i>")
    return "\n".join(parts)


def buysell_alert_text(item: dict, keyword: str) -> str:
    e = notifier.esc
    return "\n".join([
        f"🛠 <b>[{e(keyword)}]</b> {e(item['title'])}",
        f"{e(daangn_buysell.fmt_price(item['price']))} · {e(item['region'] or '지역미상')}",
        item["url"],
    ])


def run_realty(cfg: dict, state: dict, alerts: list, now: float) -> None:
    poll = cfg["poll"]
    r = cfg["realty"]

    def process(article_id) -> None:
        """상세를 파싱해 규칙에 맞으면 알림 목록에 추가하고, 성공 시에만 seen 처리한다."""
        listing = daangn_realty.fetch_article(article_id)
        time.sleep(poll["realty_delay_sec"])
        mark_seen(state, "realty", article_id)
        if listing is None:
            return
        rule = matching.match_realty(cfg, listing)
        if rule:
            add_alert(state, alerts, "realty", realty_alert_text(listing, rule),
                      photo=listing.get("image") if poll.get("photos", True) else None)
            # 가격 인하 추적 대상으로 등록
            state["realty_watch"][listing["id"]] = {
                "trade": daangn_realty.trade_snapshot(listing["trades"]),
                "ts": time.time(),
                "url": listing["url"],
                "label": f"[{listing['category']}] {listing['region']}",
            }

    # 레인 1: 서울 25개 구 카테고리 페이지 (빠른 감지)
    if now - state["last_fast_lane"] >= poll["fast_lane_interval_min"] * 60:
        try:
            candidates = daangn_realty.fast_lane_ids(r["fast_categories"], poll["realty_delay_sec"])
            state["last_fast_lane"] = now
            new_ids = []
            for i in candidates:
                if is_seen(state, "realty", i):
                    mark_seen(state, "realty", i)  # 목격 시각 갱신 — 장기 게시 매물이 프루닝 후 재알림되는 것 방지
                else:
                    new_ids.append(i)
            new_ids.sort(reverse=True)
            print(f"[realty/fast] 후보 {len(candidates)}건 중 신규 {len(new_ids)}건")
            if not state["realty_baselined"]:
                # 첫 성공 실행: 현재 노출분 전체를 기준점으로만 기록 (차단으로 실패하면 다음 실행에 재시도)
                for article_id in new_ids:
                    mark_seen(state, "realty", article_id)
                state["realty_baselined"] = True
                print("[realty/fast] 기준점 기록 완료 — 다음 실행부터 신규만 알림")
            else:
                for article_id in new_ids[: poll["fast_detail_cap_per_run"]]:
                    try:
                        process(article_id)
                    except requests.RequestException as e:
                        print(f"[realty/fast] {article_id} 요청 실패, 다음 실행에 재시도: {e}")
                    except (ValueError, KeyError, TypeError, AttributeError) as e:
                        mark_seen(state, "realty", article_id)  # 결정적 파싱 실패 — 재시도 무의미
                        print(f"[realty/fast] {article_id} 파싱 실패, 건너뜀: {e}")
        except daangn_realty.Blocked as e:
            # seen 처리 전에 끊기므로 남은 매물은 다음 실행에서 다시 잡힌다
            print(f"[realty/fast] 차단 감지, 이번 실행 건너뜀: {e}")
        except requests.RequestException as e:
            print(f"[realty/fast] 네트워크 오류, 이번 실행 건너뜀: {e}")

    # 레인 2a: sitemap에서 신규 ID 수집 (백스톱)
    if now - state["last_sitemap_check"] >= cfg["poll"]["sitemap_interval_hours"] * 3600:
        try:
            new_ids = daangn_realty.sitemap_new_ids(state)
            state["last_sitemap_check"] = now
            known = set(state["realty_pending"])
            fresh = [i for i in new_ids if str(i) not in state["realty_seen"] and i not in known]
            state["realty_pending"] = fresh + state["realty_pending"]
            print(f"[realty/sitemap] 신규 {len(fresh)}건 큐에 추가 (대기 {len(state['realty_pending'])}건)")
        except daangn_realty.Blocked as e:
            print(f"[realty/sitemap] 차단 감지: {e}")
        except requests.RequestException as e:
            print(f"[realty/sitemap] 네트워크 오류: {e}")

    # 레인 2b: 대기 큐 제목 스캔 → 지역/종류 맞으면 상세 파싱
    budget = poll["scan_budget_per_run"]
    scanned = 0
    while state["realty_pending"] and scanned < budget:
        article_id = state["realty_pending"][0]
        if is_seen(state, "realty", article_id):
            state["realty_pending"].pop(0)
            continue
        scanned += 1
        try:
            title = daangn_realty.fetch_title(article_id)
            time.sleep(poll["realty_delay_sec"] * 0.5)
            if title and matching.prefilter_title(cfg, title):
                process(article_id)
            else:
                mark_seen(state, "realty", article_id)
        except daangn_realty.Blocked as e:
            print(f"[realty/scan] 차단 감지, 이번 실행 중단: {e}")
            break
        except requests.RequestException as e:
            print(f"[realty/scan] {article_id} 요청 실패, 다음 실행에 재시도: {e}")
            break
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            mark_seen(state, "realty", article_id)  # 결정적 파싱 실패 — 큐 교착 방지
            print(f"[realty/scan] {article_id} 파싱 실패, 건너뜀: {e}")
        state["realty_pending"].pop(0)

    # 레인 3: 알림했던 매물의 가격 인하 감지 (회전 재확인)
    watch_ids = sorted(state["realty_watch"], key=lambda k: state["realty_watch"][k]["ts"], reverse=True)
    if watch_ids:
        checked = 0
        cursor = state["watch_cursor"]
        while checked < poll.get("price_watch_per_run", 8) and checked < len(watch_ids):
            article_id = watch_ids[cursor % len(watch_ids)]
            cursor += 1
            checked += 1
            entry = state["realty_watch"][article_id]
            try:
                listing = daangn_realty.fetch_article(article_id)
                time.sleep(poll["realty_delay_sec"])
            except daangn_realty.Blocked as e:
                print(f"[realty/watch] 차단 감지, 이번 실행 중단: {e}")
                break
            except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"[realty/watch] {article_id} 확인 실패: {e}")
                continue
            if listing is None:   # 삭제/거래완료 — 추적 종료
                state["realty_watch"].pop(article_id, None)
                continue
            new_snap = daangn_realty.trade_snapshot(listing["trades"])
            if daangn_realty.is_price_drop(entry["trade"], new_snap):
                add_alert(state, alerts, "drop",
                          "\n".join([
                              f"💰 <b>가격 인하!</b> {notifier.esc(entry['label'])}",
                              f"{notifier.esc(daangn_realty.fmt_trade([entry['trade']]))} → <b>{notifier.esc(daangn_realty.fmt_trade([new_snap]))}</b>",
                              entry["url"],
                          ]))
            if new_snap != entry["trade"]:
                entry["trade"] = new_snap   # 변동 반영 (같은 값으로 반복 알림 방지)
        state["watch_cursor"] = cursor % max(len(watch_ids), 1)
    if scanned:
        print(f"[realty/scan] 제목 스캔 {scanned}건 (대기 {len(state['realty_pending'])}건 남음)")


def run_buysell(cfg: dict, state: dict, alerts: list, regions: list) -> None:
    poll = cfg["poll"]
    searches = cfg["buysell"]["searches"]
    if not searches or not regions:
        return
    pairs_total = len(regions) * len(searches)
    cursor = state["buysell_cursor"] % pairs_total
    found = 0
    try:
        for _ in range(min(poll["buysell_budget_per_run"], pairs_total)):
            region = regions[cursor // len(searches)]
            search_cfg = searches[cursor % len(searches)]
            items = daangn_buysell.search(search_cfg["keyword"], region["id"])
            cursor = (cursor + 1) % pairs_total  # 성공한 뒤에 전진 — 실패한 페어는 다음 실행이 재시도
            daangn_buysell.polite_sleep(poll["buysell_delay_sec"])
            for item in items:
                if is_seen(state, "buysell", item["id"]):
                    # 이미 알림한 매물이 다시 보이면 가격 인하만 확인 (추가 요청 없음)
                    # 판매중 상태에서만 기준가를 비교·갱신 (예약중 가격 변동으로 인하 알림이 묻히는 것 방지)
                    watch = state["buysell_watch"].get(str(item["id"]))
                    if watch and item.get("price") is not None and watch.get("price") is not None \
                            and item.get("status") == "Ongoing" and item["price"] != watch["price"]:
                        if item["price"] < watch["price"]:
                            add_alert(state, alerts, "drop", "\n".join([
                                f"💰 <b>가격 인하!</b> {notifier.esc(watch['title'])}",
                                f"{notifier.esc(daangn_buysell.fmt_price(watch['price']))} → <b>{notifier.esc(daangn_buysell.fmt_price(item['price']))}</b>",
                                watch["url"],
                            ]))
                        watch["price"] = item["price"]
                    continue
                verdict = matching.match_buysell(search_cfg, item, cfg["buysell"]["freshness_days"])
                if verdict == "match":
                    mark_seen(state, "buysell", item["id"])
                    add_alert(state, alerts, "buysell", buysell_alert_text(item, search_cfg["keyword"]),
                              photo=item.get("thumbnail") if poll.get("photos", True) else None)
                    state["buysell_watch"][str(item["id"])] = {
                        "price": item.get("price"), "ts": time.time(),
                        "url": item["url"], "title": item["title"][:60],
                    }
                    found += 1
                elif verdict == "perm":
                    mark_seen(state, "buysell", item["id"])
                # "temp"(예약중, 이 검색어 가격 미달)는 seen 처리하지 않음 — 다른 검색어/다음 순회에서 재평가
    except daangn_buysell.Blocked as e:
        print(f"[buysell] 차단 감지, 이번 실행 중단: {e}")
    except requests.RequestException as e:
        print(f"[buysell] 요청 실패, 이번 실행 중단: {e}")
    finally:
        state["buysell_cursor"] = cursor
    print(f"[buysell] 순회 위치 {cursor}/{pairs_total}, 신규 알림 {found}건")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 출력만")
    args = parser.parse_args()

    cfg = load_config()
    state = load_state()
    regions = load_regions()
    alerts: list = []
    now = time.time()

    # 봇에게 온 메시지는 소비만 하고 응답하지 않는다 (명령어 기능 제거 — 상주 서버가 아니라
    # 응답이 최대 수십 분 늦어 실용성이 없었음). 조건 안내는 그룹 공지로 대체.
    command_replies: list = []
    if not args.dry_run:
        try:
            commands.drain_updates(state)
        except Exception:
            traceback.print_exc()

    # 채널 격리: 한 채널의 예기치 못한 오류가 다른 채널과 상태 저장을 막지 않게 한다
    if cfg["realty"]["enabled"]:
        try:
            run_realty(cfg, state, alerts, now)
        except Exception:
            traceback.print_exc()
            print("[realty] 예기치 못한 오류 — 이번 실행의 부동산 채널을 건너뜁니다.")
    if cfg["buysell"]["enabled"]:
        try:
            run_buysell(cfg, state, alerts, regions)
        except Exception:
            traceback.print_exc()
            print("[buysell] 예기치 못한 오류 — 이번 실행의 중고거래 채널을 건너뜁니다.")

    # 아침 요약: KST 기준 매일 digest_hour 이후 첫 실행에서 1회
    now_kst = datetime.now(KST)
    today = now_kst.strftime("%Y-%m-%d")
    if now_kst.hour >= cfg["poll"].get("digest_hour_kst", 8) and state.get("last_digest_day") != today:
        s = state.get("stats", {})
        command_replies.append("\n".join([
            f"🌅 <b>아침 브리핑</b> ({today})",
            f"지난 요약 이후 알림: 🏠 매물 {s.get('realty', 0)}건 · 🛠 장비 {s.get('buysell', 0)}건 · 💰 가격 인하 {s.get('drop', 0)}건",
            f"가격 추적 중인 매물 {len(state.get('realty_watch', {})) + len(state.get('buysell_watch', {}))}건 · 오늘도 좋은 자리 잡읍시다 ☕🫘",
        ]))
        state["stats"] = {}
        state["last_digest_day"] = today

    cap = cfg["poll"]["max_alerts_per_run"]
    to_send = alerts[:cap]
    overflow = alerts[cap:]   # 상한 초과분은 버리지 않고 outbox로 넘겨 다음 실행에서 이어 보냄
    if overflow:
        to_send.append(f"…그 외 {len(overflow)}건은 다음 실행에서 이어서 보냅니다")
    # 명령 응답/브리핑은 상한과 무관하게 먼저, 그다음 지난 실행에서 못 보낸 알림(outbox) 재시도
    queue = command_replies + state.get("outbox", [])[:cap] + to_send
    failed = []
    if not args.dry_run and notifier.dry_run() and queue:
        print(f"[알림] 텔레그램 토큰 미설정 — {len(queue)}건을 아웃박스에 보관합니다 (토큰 연결 후 첫 실행에서 전송)")
    for alert in queue:
        text = alert if isinstance(alert, str) else alert.get("text", "")
        if args.dry_run:
            print("[DRY-RUN]\n" + text + "\n")
            continue
        if notifier.dry_run():
            failed.append(alert)   # 토큰 미설정 — 유실 방지를 위해 보관
            continue
        try:
            notifier.send_alert(alert)
        except Exception as e:
            print(f"[전송 실패, 다음 실행에 재시도] {e}")
            failed.append(alert)
    # 재시도 대상: 이번에 전송 실패한 것 + 상한 초과분 + 이번에 순번이 안 온 기존 outbox
    state["outbox"] = (failed + overflow + state.get("outbox", [])[cap:])[:100]

    save_state(state, cfg["poll"]["state_max_age_days"])
    mode = "dry-run" if args.dry_run or notifier.dry_run() else "live"
    print(f"알림 {len(alerts)}건 생성, {mode} 처리 {len(queue)}건 (전송 실패 {len(failed)}건)")


if __name__ == "__main__":
    main()

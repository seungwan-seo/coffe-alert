"""텔레그램 명령어 처리.

봇은 상주 서버가 아니라 10분 주기로 깨어나는 구조라, 명령어 응답도
다음 실행 때(최대 10분 뒤) 나간다. getUpdates 오프셋은 state에 보관.
"""
import requests

import notifier

DELAY_NOTE = "⏱ 무료 서버 특성상 명령 응답은 봇이 깨어나는 다음 실행 때 와요 (보통 10분 내외, 서버 사정에 따라 수십 분까지 밀릴 수 있음)."


def help_text() -> str:
    return "\n".join([
        "☕ <b>카페봇 도움말</b>",
        "",
        "🏠 서울 상가·카페 양도 매물과 🛠 무인카페 장비(커피머신·키오스크·제빙기 등)를",
        "당근에서 10분마다 찾아 이 방으로 알려드립니다.",
        "",
        "<b>명령어</b>",
        "/help — 이 도움말",
        "/rules (또는 /조건) — 현재 매칭 조건 보기",
        "",
        DELAY_NOTE,
    ])


def rules_text(cfg: dict) -> str:
    e = notifier.esc
    r = cfg["realty"]
    lines = ["🔎 <b>현재 매칭 조건</b>", "", f"🏠 <b>부동산</b> — 지역: {e(', '.join(r['regions']))} · 등록 {r.get('freshness_days', 0)}일 이내만"]
    for rule in r["rules"]:
        cats = ", ".join(rule["categories"]) if rule["categories"] else "종류 무관"
        line = f"· [{e(rule['name'])}] {e(cats)} / 키워드: {e(', '.join(rule['keywords']))}"
        if rule.get("exclude_keywords"):
            line += f" / 제외: {e(', '.join(rule['exclude_keywords']))}"
        lines.append(line)
    b = cfg["buysell"]
    lines.append("")
    lines.append(f"🛠 <b>중고거래</b> — 서울 전 동네 순회 · 등록 {b.get('freshness_days', 0)}일 이내만")
    for s in b["searches"]:
        mp = f" (최소 {s['min_price']:,}원)" if s.get("min_price") else ""
        lines.append(f"· {e(s['keyword'])}{mp}")
    lines.append("")
    lines.append("조건 변경은 저장소 config.yaml에서.")
    lines.append(DELAY_NOTE)
    return "\n".join(lines)


def poll_commands(cfg: dict, state: dict) -> list:
    """새 명령어를 읽고 답장 텍스트 목록을 반환한다. 오프셋은 state['tg_offset']에 갱신."""
    if notifier.dry_run():
        return []
    resp = requests.get(
        f"https://api.telegram.org/bot{notifier.TOKEN}/getUpdates",
        params={"offset": state.get("tg_offset", 0) + 1, "timeout": 0},
        timeout=15,
    )
    resp.raise_for_status()
    replies = []
    for u in resp.json().get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), u.get("update_id", 0))
        try:
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if chat_id not in notifier.CHAT_IDS or not text.startswith("/"):
                continue
            cmd = text.split()[0].split("@")[0].lower()
            if cmd in ("/help", "/start"):
                replies.append(help_text())
            elif cmd in ("/rules", "/조건"):
                replies.append(rules_text(cfg))
        except Exception as e:
            # 업데이트 하나가 깨져도(설정 오류 등) 나머지 명령 처리는 계속한다
            print(f"[commands] 업데이트 처리 실패, 건너뜀: {e}")
    return replies

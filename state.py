"""실행 간 상태 관리 (state/seen.json). GitHub Actions에서는 실행 후 이 파일을 커밋해 이어간다."""
import json
import os
import time

STATE_PATH = os.path.join(os.path.dirname(__file__), "state", "seen.json")

DEFAULT = {
    "realty_baselined": False,  # False면 다음 빠른 레인 성공 시 현재 노출분 전체를 기준점으로만 기록 (폭주 방지)
    "realty_seen": {},          # {article_id: unix_ts} — 평가 완료한 매물 (마지막 목격 시각)
    "realty_pending": [],       # sitemap에서 발견됐지만 아직 제목 스캔 전인 ID들
    "realty_max_sitemap_id": 0,
    "sitemap_last_modified": "",
    "last_fast_lane": 0.0,
    "last_sitemap_check": 0.0,
    "buysell_seen": {},         # {고유코드: unix_ts}
    "buysell_cursor": 0,        # (동×키워드) 순회 위치
    "outbox": [],               # 전송 실패한 알림 — 다음 실행에서 재시도
}

PENDING_CAP = 8000


def load_state() -> dict:
    state = dict(DEFAULT)
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            state.update(json.load(f))
    return state


def save_state(state: dict, max_age_days: int = 90) -> None:
    cutoff = time.time() - max_age_days * 86400
    for key in ("realty_seen", "buysell_seen"):
        state[key] = {k: v for k, v in state[key].items() if v >= cutoff}
    state["realty_pending"] = state["realty_pending"][:PENDING_CAP]
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def is_seen(state: dict, channel: str, item_id) -> bool:
    return str(item_id) in state[f"{channel}_seen"]


def mark_seen(state: dict, channel: str, item_id) -> None:
    state[f"{channel}_seen"][str(item_id)] = time.time()

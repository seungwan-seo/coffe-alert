"""텔레그램 수신 업데이트 정리.

명령어 응답 기능은 제거했다 (봇이 10분 주기로만 깨어나는 구조라 응답이 최대 수십 분 늦어
실용성이 없었음). 대신 쌓인 업데이트를 주기적으로 비워 getUpdates 큐가 무한정 자라지 않게 한다.
사용자 안내는 그룹 공지(NOTICE.md)로 대체.
"""
import requests

import notifier


def drain_updates(state: dict) -> None:
    """미처리 업데이트를 확인 처리(오프셋 전진)만 한다."""
    if notifier.dry_run():
        return
    resp = requests.get(
        f"https://api.telegram.org/bot{notifier.TOKEN}/getUpdates",
        params={"offset": state.get("tg_offset", 0) + 1, "timeout": 0},
        timeout=15,
    )
    resp.raise_for_status()
    for u in resp.json().get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), u.get("update_id", 0))

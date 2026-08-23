"""텔레그램 전송. 토큰이 없으면 드라이런으로 콘솔에만 출력한다."""
import html
import os
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
# 쉼표로 여러 개 지정 가능 (예: "123456789,-100987654321") — 전원에게 발송
CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]


def dry_run() -> bool:
    return not (TOKEN and CHAT_IDS)


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _request(method: str, payload: dict) -> None:
    """텔레그램 API 호출 (429는 retry_after만큼 대기 후 1회 재시도)."""
    url = API.format(token=TOKEN, method=method)
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code == 429:
        try:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
        except ValueError:
            retry_after = 5
        time.sleep(retry_after + 1)
        resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()


def _post(chat_id: str, text: str, silent: bool = False) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if silent:
        payload["disable_notification"] = True   # 알림음 없이 도착 (양도 매물만 울리게 하려고)
    # 링크가 여러 개인 메시지(브리핑 등)는 미리보기를 끈다 — 안 그러면 첫 링크 카드가 크게 붙는다
    if text.count("http") > 1:
        payload["link_preview_options"] = {"is_disabled": True}
    _request("sendMessage", payload)


def send(text: str) -> None:
    """HTML parse_mode 메시지 1건을 모든 수신자에게 전송.
    호출부가 esc()로 이스케이프한 텍스트를 넘긴다."""
    if dry_run():
        print("[DRY-RUN] " + text.replace("\n", " | "))
        return
    for chat_id in CHAT_IDS:
        _post(chat_id, text)
        time.sleep(1.1)  # 같은 채팅으로 초당 1건 제한 준수


def _post_photo(chat_id: str, photo: str, caption: str, silent: bool = False) -> None:
    payload = {"chat_id": chat_id, "photo": photo, "caption": caption[:1024], "parse_mode": "HTML"}
    if silent:
        payload["disable_notification"] = True
    _request("sendPhoto", payload)


def send_alert(alert, silent: bool = False) -> None:
    """알림 1건 전송. alert는 문자열(텍스트만) 또는 {"text": ..., "photo": url|None}.
    사진 전송이 실패하면 텍스트로 폴백한다. silent면 수신자 폰이 울리지 않는다."""
    if isinstance(alert, str):
        send(alert)
        return
    text = alert.get("text", "")
    photo = alert.get("photo")
    if dry_run():
        print("[DRY-RUN] " + ("📷 " if photo else "") + text.replace("\n", " | "))
        return
    for chat_id in CHAT_IDS:
        if photo:
            try:
                _post_photo(chat_id, photo, text, silent)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if 400 <= status < 500 and status != 429:
                    _post(chat_id, text, silent)   # 사진 URL 거부 등 결정적 실패만 텍스트 폴백
                else:
                    raise                          # 일시적 오류는 outbox 재시도로 (사진 유지)
        else:
            _post(chat_id, text, silent)
        time.sleep(1.1)

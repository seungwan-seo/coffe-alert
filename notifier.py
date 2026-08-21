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


def _post(chat_id: str, text: str) -> None:
    resp = requests.post(
        API.format(token=TOKEN, method="sendMessage"),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    if resp.status_code == 429:
        try:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
        except ValueError:
            retry_after = 5
        time.sleep(retry_after + 1)
        resp = requests.post(
            API.format(token=TOKEN, method="sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
    resp.raise_for_status()


def send(text: str) -> None:
    """HTML parse_mode 메시지 1건을 모든 수신자에게 전송.
    호출부가 esc()로 이스케이프한 텍스트를 넘긴다."""
    if dry_run():
        print("[DRY-RUN] " + text.replace("\n", " | "))
        return
    for chat_id in CHAT_IDS:
        _post(chat_id, text)
        time.sleep(1.1)  # 같은 채팅으로 초당 1건 제한 준수

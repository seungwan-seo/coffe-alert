"""봇에게 메시지를 보낸 뒤 실행하면 chat_id를 출력한다.

사용법: .env에 TELEGRAM_TOKEN만 넣고 `python get_chat_id.py`
"""
import requests

from notifier import TOKEN

if not TOKEN:
    raise SystemExit(".env에 TELEGRAM_TOKEN을 먼저 넣어주세요.")

resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=30)
resp.raise_for_status()
updates = resp.json().get("result", [])
if not updates:
    raise SystemExit("업데이트가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")

for u in updates:
    chat = (u.get("message") or u.get("channel_post") or {}).get("chat", {})
    if chat:
        print(f"chat_id: {chat['id']}  ({chat.get('type')}, {chat.get('title') or chat.get('first_name', '')})")

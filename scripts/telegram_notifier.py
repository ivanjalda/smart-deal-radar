#!/usr/bin/env python3
"""
Smart Deal & Price Radar - Avisos de Telegram.

Envía al canal los chollos de data/alerts.json que aún no se hayan enviado.
Necesita dos secretos en GitHub (Settings -> Secrets and variables -> Actions):
  TELEGRAM_BOT_TOKEN  y  TELEGRAM_CHAT_ID
Si faltan, no envía nada y termina sin error.
Solo librería estándar.
"""

import json
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SENT_FILE = os.path.join(DATA, "sent_alerts.json")
MAX_PER_RUN = 10


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def send(token, chat_id, text):
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    body = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8")).get("ok", False)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram sin configurar: no se envía nada.")
        return

    alerts = load(os.path.join(DATA, "alerts.json"), {"alerts": []}).get("alerts", [])
    sent = load(SENT_FILE, {"sent": []})
    sent_keys = set(sent.get("sent", []))

    count = 0
    for a in alerts:
        key = "%s|%s" % (a["id"], a["price"])
        if key in sent_keys or count >= MAX_PER_RUN:
            continue
        text = (
            "🔥 <b>%s</b>\n"
            "💶 <b>%.2f €</b> en %s\n"
            "📉 %.0f %% por debajo de su media real de 90 días (%.2f €)\n"
            "%s\n\n👉 %s"
        ) % (esc(a["name"]), a["price"], esc(a["store"]), a["saving_pct"],
             a["avg90"], esc(" · ".join(a.get("badges", []))), esc(a["url"]))
        try:
            if send(token, chat_id, text):
                sent_keys.add(key)
                count += 1
        except Exception as exc:  # noqa: BLE001
            print("Error enviando %s: %s" % (a["id"], exc))

    with open(SENT_FILE, "w", encoding="utf-8") as fh:
        json.dump({"sent": sorted(sent_keys)[-2000:]}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("Telegram: %d avisos enviados" % count)


if __name__ == "__main__":
    main()

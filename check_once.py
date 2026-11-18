"""Одна перевірка статусу ПФУ. Запускається GitHub Actions кожні 5 хв.
Якщо статус змінився — надсилає повідомлення в Telegram."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TRACK_ID = os.environ["TRACK_ID"]
URL = f"https://portal.pfu.gov.ua/sidebar/Templates/AppealTracking?id={TRACK_ID}"
STATE = Path("state.json")


def fetch_status():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="uk-UA")
        try:
            page.goto(URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_selector("dt:has-text('Статус')", timeout=30_000)

            def dd(label):
                loc = page.locator(f"dt:has-text('{label}') + dd").first
                return loc.inner_text().strip() if loc.count() else "—"

            card = page.locator("article").first
            title = card.locator("h3").first.inner_text().strip() if card.count() else "Звернення"
            stages = []
            items = page.get_by_role("list", name="Етапи опрацювання звернення").locator("li")
            for i in range(items.count()):
                li = items.nth(i)
                text = li.inner_text()
                state = next((s for s in ("Поточний", "Виконано", "Завершено", "Очікується")
                              if re.search(rf"^\s*{s}\s*$", text, re.M)), "")
                dates = re.findall(r"\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2})?", text)
                stages.append({"name": li.locator("strong").first.inner_text().strip(),
                               "state": state, "date": dates[0] if dates else ""})
            return {"title": title, "status": dd("Статус"), "submitted": dd("Дата подання"),
                    "deadline": dd("Орієнтовна дата завершення"), "stages": stages}
        finally:
            browser.close()


def fingerprint(s):
    raw = json.dumps([s["status"], s["deadline"],
                      [(x["name"], x["state"], x["date"]) for x in s["stages"]]],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()  # у репозиторії лише хеш, без даних


def format_status(s, header):
    icons = {"Поточний": "🟢", "Виконано": "✅", "Завершено": "✅", "Очікується": "⚪️"}
    lines = [header, f"📄 <b>{s['title']}</b>", f"Статус: <b>{s['status']}</b>",
             f"Подано: {s['submitted']}", f"Орієнтовне завершення: {s['deadline']}",
             "", "<b>Хід опрацювання:</b>"]
    for i, st in enumerate(s["stages"], 1):
        extra = f" ({st['date']})" if st["date"] and st["state"] != "Очікується" else ""
        lines.append(f"{icons.get(st['state'], '▫️')} {i}. {st['name']} — {st['state'] or '?'}{extra}")
    lines += ["", f'<a href="{URL}">Відкрити на порталі</a>']
    return "\n".join(lines)


HOURLY = 55 * 60  # щогодинний звіт (55 хв — запас на затримки GitHub)


def main():
    s = fetch_status()
    fp = fingerprint(s)
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    last, last_sent = st.get("fp"), st.get("last_sent", 0)
    now = time.time()

    if last is None:
        header = "👀 Почав стежити."
    elif fp != last:
        header = "🔔 <b>Статус змінився!</b>"
    elif now - last_sent >= HOURLY or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        header = "🕐 Щогодинний статус (без змін)"
    else:
        header = None

    if header:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": format_status(s, header),
                                "parse_mode": "HTML", "disable_web_page_preview": True},
                          timeout=30)
        r.raise_for_status()
        STATE.write_text(json.dumps({"fp": fp, "last_sent": int(now)}))
    print("Статус:", s["status"], "| надіслано" if header else "| без змін")


if __name__ == "__main__":
    main()


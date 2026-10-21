import os, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["TG_CHAT_ID"].split(",") if c.strip()]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
URL = "https://broneering.mfa.ee/en/"
telegram_failed = False

def notify(msg):
    global telegram_failed
    for cid in CHAT_IDS:
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                              data={"chat_id": cid, "text": msg[:4000]}, timeout=30)
            if r.status_code == 200:
                print("Telegram: message sent OK")
            else:
                print("Telegram REJECTED the message:", r.json().get("description"))
                telegram_failed = True
        except Exception as e:
            print("Telegram error:", e)
            telegram_failed = True

def get_services(page, city):
    sel = page.locator("#broneering_esindus")
    label = None
    for opt in sel.locator("option").all_inner_texts():
        if city.lower() in opt.lower():
            label = opt.strip()
            break
    if label is None:
        return None
    sel.select_option(label=label)
    services = []
    for _ in range(10):
        page.wait_for_timeout(1000)
        opts = [o.strip() for o in page.locator("#broneering_toiming option").all_inner_texts()]
        services = [o for o in opts if o]
        if services:
            break
    return services

if TEST_MODE:
    notify("TEST: the bot can post here.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
      loaded = False
    for attempt in range(3):
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_selector("#broneering_esindus", timeout=30000)
            loaded = True
            break
        except Exception as e:
            print(f"Attempt {attempt+1} failed:", e)
            page.wait_for_timeout(5000)
    if not loaded:
        try:
            body = page.inner_text("body")[:500].replace("\n", " ")
        except Exception:
            body = "(could not read page)"
        print("PAGE TITLE:", page.title())
        print("PAGE URL:", page.url)
        print("PAGE TEXT:", body)
        if TEST_MODE:
            notify(f"TEST: site did not load. Title: {page.title()} | Text: {body}")
        browser.close()
        raise SystemExit(1)

    services = get_services(page, "New Delhi")
    print("New Delhi services:", services)
    if services and not TEST_MODE:
        notify("Estonia New Delhi: booking options appeared: "
               + ", ".join(services) + "\nBook now: " + URL)
    browser.close()

if telegram_failed:
    raise SystemExit(1)

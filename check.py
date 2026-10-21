import time, random, requests
from playwright.sync_api import sync_playwright

TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"
URL = "https://broneering.mfa.ee/en/"
AVAILABLE_SELECTOR = ".day.available"   # <-- change after inspecting the page
CHECK_EVERY = (180, 300)                # seconds, random range

def notify(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg})

def check(page):
    page.goto(URL, wait_until="networkidle")
    # TODO: click the "New Delhi" / visa-type option here, e.g.:
    # page.click("text=New Delhi")
    # page.wait_for_load_state("networkidle")
    return page.locator(AVAILABLE_SELECTOR).count() > 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    notified = False
    while True:
        try:
            found = check(page)
            if found and not notified:
                notify("Estonia D-visa slot available in New Delhi! " + URL)
                notified = True
            elif not found:
                notified = False
        except Exception as e:
            print("Error:", e)
        time.sleep(random.randint(*CHECK_EVERY))

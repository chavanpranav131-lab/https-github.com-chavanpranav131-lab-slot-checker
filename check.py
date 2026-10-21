import os, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
URL = "https://broneering.mfa.ee/en/"
AVAILABLE_SELECTOR = ".day.available"   # CHANGE THIS in Step 6

def notify(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg}, timeout=30)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(URL, wait_until="networkidle", timeout=60000)

    # TODO Step 6: add clicks here to reach the New Delhi calendar, e.g.
    # page.click("text=New Delhi")
    # page.wait_for_load_state("networkidle")

    count = page.locator(AVAILABLE_SELECTOR).count()
    print("Available slots found:", count)

    if TEST_MODE:
        notify(f"TEST: bot works. Slots found: {count}")
    elif count > 0:
        notify("Estonia visa slot AVAILABLE! Book now: " + URL)
    browser.close()

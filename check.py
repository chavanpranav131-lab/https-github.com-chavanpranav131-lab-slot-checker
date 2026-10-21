import os, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
URL = "https://broneering.mfa.ee/en/"
AVAILABLE_SELECTOR = ".day.available"   # UPDATE THIS: Replace with your actual calendar selector

def notify(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg}, timeout=30)

# Send the test message FIRST, so we know Telegram works
if TEST_MODE:
    notify("TEST: Telegram works. Now trying to open the website...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
    except Exception as e:
        print("Page load failed:", e)
        if TEST_MODE:
            notify("TEST: website did NOT load from GitHub (likely blocked or too slow).")
        browser.close()
        raise SystemExit(1)

    if TEST_MODE:
        notify("TEST: website loaded OK. Page title: " + page.title())

    # --- Step 6: Navigation to New Delhi calendar ---
    try:
        # Update these selectors based on the actual layout of broneering.mfa.ee
        page.click("text=New Delhi")
        page.wait_for_load_state("networkidle")
    except Exception as nav_err:
        print("Navigation to New Delhi failed:", nav_err)

    # Count available slots matching the selector
    count = page.locator(AVAILABLE_SELECTOR).count()
    print("Available slots found:", count)

    if not TEST_MODE and count > 0:
        notify("Estonia visa slot AVAILABLE! Book now: " + URL)
        
    browser.close()

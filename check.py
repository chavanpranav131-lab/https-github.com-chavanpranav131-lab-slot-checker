import os, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
URL = "https://broneering.mfa.ee/en/"

def notify(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=30)

def describe_selects(page):
    out = []
    selects = page.locator("select")
    for i in range(selects.count()):
        s = selects.nth(i)
        opts = [o.strip() for o in s.locator("option").all_inner_texts()]
        out.append(f"select #{i} id={s.get_attribute('id')} name={s.get_attribute('name')} options={opts}")
    return "\n".join(out) or "No <select> elements found on the page"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
    except Exception as e:
        print("Page load failed:", e)
        browser.close()
        raise SystemExit(1)

    if not TEST_MODE:
        print("Discovery mode: nothing to do on scheduled runs.")
        browser.close()
        raise SystemExit(0)

    before = describe_selects(page)

    chosen = None
    selects = page.locator("select")
    for i in range(selects.count()):
        for opt in selects.nth(i).locator("option").all_inner_texts():
            if "new delhi" in opt.lower():
                selects.nth(i).select_option(label=opt.strip())
                chosen = opt.strip()
                break
        if chosen:
            break

    page.wait_for_timeout(4000)
    after = describe_selects(page)

    notify("BEFORE selecting:\n" + before)
    notify("Selected: " + str(chosen) + "\n\nAFTER selecting:\n" + after)
    browser.close()

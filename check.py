import os, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
URL = "https://broneering.mfa.ee/en/"

def notify(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=30)

def get_services(page, city):
    """Select an embassy containing `city`, return the list of real service options."""
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
    for _ in range(10):                      # wait up to ~10 seconds for the list to fill
        page.wait_for_timeout(1000)
        opts = [o.strip() for o in page.locator("#broneering_toiming option").all_inner_texts()]
        services = [o for o in opts if o]
        if services:
            break
    return services

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#broneering_esindus", timeout=20000)
    except Exception as e:
        print("Page load failed:", e)
        browser.close()
        raise SystemExit(1)

    if TEST_MODE:
        lines = []
        for city in ["New Delhi", "London", "Berlin", "Stockholm"]:
            lines.append(f"{city}: {get_services(page, city)}")
        notify("CONTROL TEST\n" + "\n".join(lines))
    else:
        services = get_services(page, "London")
        print("New Delhi services:", services)
        if services:
            notify("Estonia New Delhi: booking options appeared: "
                   + ", ".join(services) + "\nBook now: " + URL)

    browser.close()

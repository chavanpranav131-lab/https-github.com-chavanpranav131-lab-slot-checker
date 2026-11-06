"""
Estonian MFA booking monitor  -  v2 (loop + self-chaining)

Flow
  1. Load https://broneering.mfa.ee/en/ and pick the representation (default: New Delhi).
  2. If the service dropdown is populated -> slots exist -> Telegram alert (always).
  3. If AUTOFILL=1 -> select D-visa, pre-fill personal details, pick first free date/time,
     and relay each image CAPTCHA to Telegram so YOU type the answer (human-in-the-loop).

All personal data comes from environment variables / GitHub Secrets. Nothing is hard-coded.
"""
import os, re, sys, time, requests
from playwright.sync_api import sync_playwright

# ---------- config ----------
TOKEN = os.environ["TG_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["TG_CHAT_ID"].split(",") if c.strip()]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
AUTOFILL = os.environ.get("AUTOFILL", "0") == "1"
CITY = os.environ.get("CITY", "New Delhi")
SERVICE_MATCH = os.environ.get("SERVICE_MATCH", "Long-stay")
CAPTCHA_WAIT = int(os.environ.get("CAPTCHA_WAIT_SECONDS", "200"))
NOTE_TEXT = os.environ.get("NOTE_TEXT", "Long-stay visa (D-visa) application")
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "0"))        # 0 = single check
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "60"))       # seconds between checks
REALERT = int(os.environ.get("REALERT_SECONDS", "900"))        # reminder while slots stay open
URL = "https://broneering.mfa.ee/en/"
API = f"https://api.telegram.org/bot{TOKEN}"
OUT = "debug"
os.makedirs(OUT, exist_ok=True)

PROFILE = {
    "first name": os.environ.get("FIRST_NAME", ""),
    "surname": os.environ.get("SURNAME", ""),
    "date of birth": os.environ.get("DOB", ""),            # e.g. 15.02.1987
    "phone": os.environ.get("PHONE", ""),                  # e.g. +917981013055
    "e-mail": os.environ.get("EMAIL", ""),
    "visa application number": os.environ.get("VISA_APP_NO", ""),
}

telegram_failed = False


# ---------- telegram ----------
def notify(msg):
    global telegram_failed
    for cid in CHAT_IDS:
        try:
            r = requests.post(f"{API}/sendMessage",
                              data={"chat_id": cid, "text": msg[:4000]}, timeout=30)
            if r.status_code != 200:
                print("Telegram REJECTED:", r.json().get("description"))
                telegram_failed = True
        except Exception as e:
            print("Telegram error:", e)
            telegram_failed = True


def send_photo(data: bytes, caption: str):
    for cid in CHAT_IDS:
        try:
            requests.post(f"{API}/sendPhoto", data={"chat_id": cid, "caption": caption[:1000]},
                          files={"photo": ("img.png", data)}, timeout=60)
        except Exception as e:
            print("sendPhoto error:", e)


def last_update_id():
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": -1}, timeout=30).json()
        res = r.get("result", [])
        return res[-1]["update_id"] if res else 0
    except Exception:
        return 0


def wait_for_reply(since_id, seconds):
    """Poll Telegram for a text reply from an allowed chat. Returns str or None."""
    deadline = time.time() + seconds
    offset = since_id + 1
    while time.time() < deadline:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 0},
                             timeout=30).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if str(m.get("chat", {}).get("id")) in CHAT_IDS and m.get("text"):
                    return m["text"].strip()
        except Exception as e:
            print("getUpdates error:", e)
        time.sleep(3)
    return None


# ---------- page helpers ----------
def snap(page, name):
    try:
        page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
        with open(f"{OUT}/{name}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        print("snap failed:", e)


def get_services(page, city):
    sel = page.locator("#broneering_esindus")
    label = None
    for opt in sel.locator("option").all_inner_texts():
        if city.lower() in opt.lower():
            label = opt.strip()
            break
    if label is None:
        print(f"Representation containing '{city}' not found in dropdown.")
        return None
    sel.select_option(label=label)
    services = []
    for _ in range(10):                       # service list is loaded via AJAX
        page.wait_for_timeout(1000)
        opts = [o.strip() for o in page.locator("#broneering_toiming option").all_inner_texts()]
        services = [o for o in opts if o]
        if services:
            break
    return services


def fill_field(page, key, value):
    """Find an input by its visible label text; fall back to the next input after the label."""
    pat = re.compile(key, re.I)
    loc = page.get_by_label(pat)
    if loc.count() == 0:
        loc = page.locator(
            f"xpath=//label[contains(translate(normalize-space(.),"
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{key.lower()}')]"
            f"/following::input[1]")
    if loc.count() == 0:
        raise RuntimeError(f"field not found: {key}")
    el = loc.first
    el.click()
    el.fill(value)
    if "birth" in key:
        page.keyboard.press("Escape")          # close datepicker popup


def captcha_input(page):
    loc = page.get_by_label(re.compile("following code", re.I))
    if loc.count() == 0:
        loc = page.locator("input[name*='captcha' i], input[id*='captcha' i]")
    return loc.first


def captcha_image(page):
    loc = page.locator("img[src*='captcha' i], img[id*='captcha' i], img[alt*='captcha' i]")
    if loc.count() == 0:   # fall back: nearest image before the captcha input
        loc = captcha_input(page).locator("xpath=preceding::img[1]")
    return loc.first


def solve_captcha(page, stage):
    """Send CAPTCHA to Telegram, wait for the human's answer, type it in. 3 tries."""
    for attempt in range(3):
        page.wait_for_timeout(800)
        png = captcha_image(page).screenshot()
        base = last_update_id()
        send_photo(png, f"CAPTCHA ({stage}) - reply with the text. "
                        f"Reply 'new' for a new image. Waiting {CAPTCHA_WAIT}s.")
        ans = wait_for_reply(base, CAPTCHA_WAIT)
        if ans is None:
            notify(f"No CAPTCHA reply for '{stage}' - giving up this run.")
            return False
        if ans.lower() == "new":
            page.get_by_role("button", name=re.compile("Uuenda|refresh|reload", re.I)).first.click()
            continue
        captcha_input(page).fill(ans)
        return True
    return False


def click_next(page, name_pat="next page|book the appointment"):
    page.get_by_role("button", name=re.compile(name_pat, re.I)).first.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)


def pick_first_slot(page):
    day_selectors = [
        ".flatpickr-day:not(.flatpickr-disabled):not(.prevMonthDay):not(.nextMonthDay)",
        "td[data-handler='selectDay']:not(.ui-state-disabled)",
        ".ui-datepicker-calendar td:not(.ui-state-disabled):not(.ui-datepicker-other-month) a",
        ".datepicker-days td.day:not(.disabled):not(.old):not(.new)",
        "td.available, td.enabled, .day.available",
    ]
    for s in day_selectors:
        days = page.locator(s)
        if days.count():
            days.first.click()
            break
    else:
        raise RuntimeError("no selectable calendar day found (selectors need tuning, see debug/)")
    page.wait_for_timeout(1500)
    times = page.locator("input[type=checkbox]:not([disabled]):not(:checked)")
    if times.count() == 0:
        raise RuntimeError("no free time boxes found")
    times.first.check()


def autofill_flow(page, service_label):
    page.locator("#broneering_toiming").select_option(label=service_label)

    # page 1 -> captcha -> next
    if not solve_captcha(page, "page 1"):
        return False
    click_next(page, "next page")

    # page 2 -> personal details
    snap(page, "page2")
    for key, val in PROFILE.items():
        if not val:
            raise RuntimeError(f"missing env value for '{key}'")
        fill_field(page, key, val)
    click_next(page, "next page")

    # page 3 -> date, time, note, captcha, book
    snap(page, "page3")
    pick_first_slot(page)
    note = page.get_by_label(re.compile("specifying information", re.I))
    if note.count() == 0:
        note = page.locator("textarea")
    note.first.fill(NOTE_TEXT[:250])
    if not solve_captcha(page, "final booking"):
        return False
    snap(page, "before_submit")
    click_next(page, "book the appointment")
    snap(page, "result")
    body = page.inner_text("body")[:600].replace("\n", " ")
    notify(f"Booking submitted. Page says: {body}\nCheck your e-mail for the 'Booked' confirmation.")
    return True


# ---------- main ----------
def load_page(page):
    for attempt in range(3):
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_selector("#broneering_esindus", timeout=30000)
            return True
        except Exception as e:
            print(f"Load attempt {attempt + 1} failed:", e)
            page.wait_for_timeout(3000)
    return False


def main():
    if TEST_MODE:
        notify("TEST: the bot can post here.")

    start = time.time()
    last_key, last_alert, load_fails, booked = None, 0.0, 0, False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1600})

        while True:
            t0 = time.time()
            try:
                if not load_page(page):
                    load_fails += 1
                    print("site did not load, fail count", load_fails)
                    snap(page, "load_failed")
                    if load_fails == 10:
                        notify("Monitor warning: site has not loaded for 10 checks in a row.")
                else:
                    load_fails = 0
                    services = get_services(page, CITY)
                    stamp = time.strftime("%H:%M:%S")
                    print(f"[{stamp}] {CITY} services:", services)

                    if TEST_MODE:
                        notify(f"TEST: {CITY} services right now: {services or 'none'}")
                    elif services:
                        key = tuple(sorted(services))
                        if key != last_key or time.time() - last_alert > REALERT:
                            notify(f"Estonia {CITY}: booking options appeared: "
                                   + ", ".join(services) + f"\nBook now: {URL}")
                            last_key, last_alert = key, time.time()
                            if AUTOFILL:
                                match = next((s for s in services
                                              if SERVICE_MATCH.lower() in s.lower()), None)
                                if not match:
                                    notify(f"Slots exist but none match '{SERVICE_MATCH}'.")
                                else:
                                    try:
                                        if autofill_flow(page, match):
                                            booked = True
                                            open(f"{OUT}/booked.flag", "w").write("1")
                                        else:
                                            notify("Auto-fill stopped (CAPTCHA not answered). "
                                                   "Book manually.")
                                    except Exception as e:
                                        snap(page, "autofill_error")
                                        notify(f"Auto-fill error: {e}\nBook manually: {URL}")
                    else:
                        last_key = None          # slots gone -> next appearance alerts again
            except Exception as e:
                print("Check error:", e)
                snap(page, "check_error")

            if booked or TEST_MODE or LOOP_SECONDS <= 0:
                break
            if time.time() - start + INTERVAL > LOOP_SECONDS:
                break
            time.sleep(max(1, INTERVAL - (time.time() - t0)))

        browser.close()

    if telegram_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

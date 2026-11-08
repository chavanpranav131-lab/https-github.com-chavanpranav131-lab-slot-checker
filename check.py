"""
Estonian MFA booking monitor - v5 (OCR Automated with Page Verification & Error Screenshots)
"""
import os, re, sys, time, requests
import cv2
import pytesseract
from PIL import Image
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TG_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["TG_CHAT_ID"].split(",") if c.strip()]
TEST_MODE = os.environ.get("TEST_MODE") == "1"
AUTOFILL = os.environ.get("AUTOFILL", "0") == "1"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
CITY = os.environ.get("CITY", "New Delhi")
SERVICE_MATCH = os.environ.get("SERVICE_MATCH", "Long-stay")
NOTE_TEXT = os.environ.get("NOTE_TEXT", "Long-stay visa (D-visa) application")
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "0"))
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "60"))
REALERT = int(os.environ.get("REALERT_SECONDS", "900"))
URL = "https://broneering.mfa.ee/en/"
API = f"https://api.telegram.org/bot{TOKEN}"
OUT = "debug"
os.makedirs(OUT, exist_ok=True)

PROFILE = {
    "first name": os.environ.get("FIRST_NAME", ""),
    "surname": os.environ.get("SURNAME", ""),
    "date of birth": os.environ.get("DOB", ""),
    "phone": os.environ.get("PHONE", ""),
    "e-mail": os.environ.get("EMAIL", ""),
    "visa application number": os.environ.get("VISA_APP_NO", ""),
}

telegram_failed = False


def notify(msg):
    global telegram_failed
    for cid in CHAT_IDS:
        try:
            r = requests.post(f"{API}/sendMessage", data={"chat_id": cid, "text": msg[:4000]}, timeout=30)
            if r.status_code != 200:
                print("Telegram REJECTED:", r.json().get("description"))
                telegram_failed = True
        except Exception as e:
            print("Telegram error:", e)
            telegram_failed = True


def send_photo(data: bytes, caption: str):
    for cid in CHAT_IDS:
        try:
            requests.post(f"{API}/sendPhoto", data={"chat_id": cid, "caption": caption[:1000]}, files={"photo": ("img.png", data)}, timeout=60)
        except Exception as e:
            print("sendPhoto error:", e)


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
    for _ in range(10):
        page.wait_for_timeout(1000)
        opts = [o.strip() for o in page.locator("#broneering_toiming option").all_inner_texts()]
        services = [o for o in opts if o]
        if services:
            break
    return services


def fill_field(page, key, value):
    key_lower = key.lower()
    loc = None

    inputs = page.locator("input[type='text'], input[type='email'], input[type='tel'], input:not([type])")
    mapping = {
        "first name": 0,
        "surname": 1,
        "date of birth": 2,
        "phone": 3,
        "e-mail": 4,
        "visa application number": 5
    }
    idx = mapping.get(key_lower, -1)
    if idx >= 0 and inputs.count() > idx:
        loc = inputs.nth(idx)

    if not loc or loc.count() == 0:
        loc = page.locator(f"xpath=//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{key_lower}')]/following::input[1]")

    if not loc or loc.count() == 0:
        raise RuntimeError(f"field not found: {key}")

    el = loc.first
    el.scroll_into_view_if_needed()
    el.click()
    el.fill(str(value))
    if "birth" in key_lower:
        page.keyboard.press("Escape")


def captcha_input(page):
    loc = page.get_by_label(re.compile("following code", re.I))
    if loc.count() == 0:
        loc = page.locator("input[name*='captcha' i], input[id*='captcha' i]")
    if loc.count() == 0:
        loc = page.locator(".captcha-row input, tr:has(img[src*='captcha']) input, table input[type='text']").last
    return loc.first


def captcha_image(page):
    loc = page.locator("img[src*='captcha' i], img[id*='captcha' i], img[alt*='captcha' i]")
    if loc.count() == 0:
        loc = captcha_input(page).locator("xpath=preceding::img[1]")
    return loc.first


def solve_captcha_with_ocr(page, stage):
    try:
        page.wait_for_timeout(1000)
        img_el = captcha_image(page)
        captcha_path = f"{OUT}/captcha_{stage}.png"
        img_el.screenshot(path=captcha_path)

        img = cv2.imread(captcha_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)
        
        processed_path = f"{OUT}/processed_{stage}.png"
        cv2.imwrite(processed_path, thresh)

        custom_config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz0123456789'
        captcha_text = pytesseract.image_to_string(Image.open(processed_path), config=custom_config).strip()
        
        print(f"[{stage}] OCR detected text: '{captcha_text}'")

        if len(captcha_text) >= 3:
            captcha_input(page).fill(captcha_text)
            return True
        else:
            refresh_btn = page.get_by_role("button", name=re.compile("Uuenda|refresh|reload", re.I))
            if refresh_btn.count() > 0:
                refresh_btn.first.click()
    except Exception as e:
        print(f"OCR error on {stage}: {e}")
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
        raise RuntimeError("no selectable calendar day found")
    page.wait_for_timeout(1500)
    times = page.locator("input[type=checkbox]:not([disabled]):not(:checked)")
    if times.count() == 0:
        raise RuntimeError("no free time boxes found")
    times.first.check()


def autofill_flow(page, service_label):
    page.locator("#broneering_toiming").select_option(label=service_label)

    # Page 1 -> OCR CAPTCHA -> Next -> Verify Page 2 advance (up to 3 tries)
    success = False
    for attempt in range(1, 4):
        print(f"Page 1 CAPTCHA attempt {attempt}/3")
        if not solve_captcha_with_ocr(page, f"page1_attempt_{attempt}"):
            continue
        
        click_next(page, "next page")
        page.wait_for_timeout(2000)

        # Verify page advanced to page 2 (checking for inputs)
        is_page2 = page.locator("input[type='text'], input[type='email'], input[type='tel'], input:not([type])").count() >= 3
        if is_page2:
            success = True
            break
        else:
            print(f"Page 1 CAPTCHA rejected or didn't advance (attempt {attempt}).")
            snap(page, f"page1_failed_attempt_{attempt}")
            try:
                with open(f"{OUT}/page1_failed_attempt_{attempt}.png", "rb") as f:
                    send_photo(f.read(), f"Page 1 CAPTCHA rejected / page did not advance (attempt {attempt}/3). Retrying.")
            except Exception:
                pass

    if not success:
        notify("Auto-fill failed: Page 1 CAPTCHA rejected after 3 attempts.")
        return False

    # Page 2 -> Personal details
    try:
        snap(page, "page2")
        for key, val in PROFILE.items():
            if not val:
                raise RuntimeError(f"missing env value for '{key}'")
            fill_field(page, key, val)
        send_photo(page.screenshot(full_page=True), "Page 2 filled by script (OCR)")
        click_next(page, "next page")
    except Exception as e:
        snap(page, "page2_error")
        try:
            with open(f"{OUT}/page2_error.png", "rb") as f:
                send_photo(f.read(), f"Page 2 Error: {e}")
        except Exception:
            pass
        raise e

    # Page 3 -> Date, time, note, captcha, book
    snap(page, "page3")
    pick_first_slot(page)
    note = page.get_by_label(re.compile("specifying information", re.I))
    if note.count() == 0:
        note = page.locator("textarea")
    note.first.fill(NOTE_TEXT[:250])
    send_photo(page.screenshot(full_page=True), "Page 3: date, time and note chosen")
    
    if DRY_RUN:
        notify("REHEARSAL OK: all steps worked with OCR. Stopped BEFORE final CAPTCHA.")
        return True

    # Final CAPTCHA loop (up to 3 tries)
    success_final = False
    for attempt in range(1, 4):
        print(f"Final CAPTCHA attempt {attempt}/3")
        if not solve_captcha_with_ocr(page, f"final_attempt_{attempt}"):
            continue
        
        snap(page, "before_submit")
        click_next(page, "book the appointment")
        page.wait_for_timeout(2000)

        body_text = page.inner_text("body").lower()
        if "booked" in body_text or "confirmation" in body_text or "success" in body_text:
            success_final = True
            break
        else:
            snap(page, f"final_failed_attempt_{attempt}")
            try:
                with open(f"{OUT}/final_failed_attempt_{attempt}.png", "rb") as f:
                    send_photo(f.read(), f"Final CAPTCHA rejected / submission failed (attempt {attempt}/3). Retrying.")
            except Exception:
                pass

    if not success_final:
        notify("Auto-fill failed: Final booking CAPTCHA rejected after 3 attempts.")
        return False

    snap(page, "result")
    body = page.inner_text("body")[:600].replace("\n", " ")
    notify(f"Booking submitted automatically via OCR! Page says: {body}")
    return True


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
                                            notify("Auto-fill stopped (OCR failed). Book manually.")
                                    except Exception as e:
                                        snap(page, "autofill_error")
                                        try:
                                            with open(f"{OUT}/autofill_error.png", "rb") as f:
                                                send_photo(f.read(), f"Auto-fill error: {e}")
                                        except Exception:
                                            pass
                                        notify(f"Auto-fill error: {e}\nBook manually: {URL}")
                    else:
                        last_key = None
                        if DRY_RUN:
                            notify(f"REHEARSAL: no services open for {CITY} right now.")
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

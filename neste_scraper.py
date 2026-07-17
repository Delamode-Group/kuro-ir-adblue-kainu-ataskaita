"""
Neste scraperis (Playwright).
1. Prisijungia prie neste.lt
2. Eina i "Sutarties kainos ir nuolaidos"
3. Pasirenka klienta, sali, data
4. Formuoja ataskaita be PVM
5. Istraukia Diesel Futura ir AdBlue kainas
"""
import json
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

import config

DEBUG_DIR = "debug"


def get_report_date():
    today = datetime.now()
    weekday = today.weekday()
    if weekday == 0:
        report_date = today - timedelta(days=3)
    else:
        report_date = today - timedelta(days=1)
    return report_date.strftime("%d.%m.%Y")


async def debug_screenshot(page, name):
    """Issaugo debug screenshot."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"{name}.png")
    try:
        await page.screenshot(path=path, full_page=False)
        print(f"[Neste] Debug screenshot: {path}")
    except Exception as e:
        print(f"[Neste] Screenshot klaida: {e}")


async def close_cookies(page):
    """Uzdaro cookies popup jei yra. Neste naudoja OneTrust framework."""
    try:
        # Laukiame kol atsiras OneTrust banner arba bet koks cookie popup
        try:
            await page.wait_for_selector(
                '#onetrust-banner-sdk, #onetrust-consent-sdk, [class*="cookie"], [id*="cookie"]',
                timeout=3000,
            )
            print("[Neste] Cookie popup aptiktas")
        except Exception:
            print("[Neste] Cookie popup nepasirade per 3s, bandome toliau")

        # 1. OneTrust mygtuku ID — patikimiausi
        for selector in [
            '#onetrust-accept-btn-handler',
            '#onetrust-reject-all-handler',
        ]:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click(force=True)
                print(f"[Neste] Cookies uzdarytas: {selector}")
                await page.wait_for_timeout(1000)
                return

        # 2. Per teksta — ieskom bet kokio elemento su cookie mygtuko tekstu
        for text in [
            "Accept All Cookies",
            "Necessary cookies only",
            "Priimti visus slapukus",
            "Sutinku",
            "Priimti",
            "Reject All",
        ]:
            btn = page.locator(f'button:has-text("{text}"), a:has-text("{text}"), [role="button"]:has-text("{text}"), div:has-text("{text}")')
            if await btn.count() > 0:
                await btn.first.click(force=True)
                print(f"[Neste] Cookies uzdarytas: '{text}'")
                await page.wait_for_timeout(1000)
                return

        # 3. Generic cookie banner uzdarymo mygtukai (tik matomi)
        for selector in [
            'button[class*="cookie" i][class*="accept" i]:visible',
            'button[class*="cookie" i][class*="close" i]:visible',
            '[class*="consent"] button:visible',
            '[id*="consent"] button:visible',
        ]:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click(force=True)
                print(f"[Neste] Cookies uzdarytas (generic): {selector}")
                await page.wait_for_timeout(1000)
                return

        print("[Neste] Cookies popup nerastas arba jau uzdarytas")
    except Exception as e:
        print(f"[Neste] Cookies klaida (nekritine): {e}")


def _load_neste_storage_state():
    """
    Nuskaito Neste sesijos busena is:
      1. NESTE_STORAGE_STATE env kintamojo (GitHub Secret, base64)
      2. Lokalaus failo cookies/neste_cookies.json
    Grazina kelia i laikina faila arba None.
    """
    import base64, tempfile

    # 1. Is GitHub Secret
    storage_b64 = os.getenv("NESTE_STORAGE_STATE", "")
    if storage_b64:
        try:
            storage_json = base64.b64decode(storage_b64).decode()
            tmp = tempfile.NamedTemporaryFile(
                suffix=".json", delete=False, mode="w", encoding="utf-8"
            )
            tmp.write(storage_json)
            tmp.close()
            cookies_count = len(json.loads(storage_json).get("cookies", []))
            print(f"[Neste] Cookies is NESTE_STORAGE_STATE ({cookies_count} vnt.)")
            return tmp.name
        except Exception as e:
            print(f"[Neste] NESTE_STORAGE_STATE klaida: {e}")

    # 2. Is lokalaus failo
    if os.path.exists(config.NESTE_COOKIES):
        print(f"[Neste] Cookies is failo: {config.NESTE_COOKIES}")
        return config.NESTE_COOKIES

    print("[Neste] Cookies nerasti — bandysime prisijungti")
    return None


async def _scrape_neste_impl():
    """Tikroji scrapinimo logika su visais Playwright veiksmais."""
    report_date = get_report_date()
    print(f"[Neste] Ataskaitos data: {report_date}")

    results = {"diesel": None, "adblue": None, "date": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Krauname sesijos busena jei yra
        _storage_path = _load_neste_storage_state()
        _tmp_file = _storage_path if (
            _storage_path and _storage_path != config.NESTE_COOKIES
        ) else None

        _ctx_kwargs = dict(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        )
        if _storage_path:
            _ctx_kwargs["storage_state"] = _storage_path

        context = await browser.new_context(**_ctx_kwargs)
        page = await context.new_page()

        try:
            # -- 1. Einame i prisijungimo puslapi --
            print(f"[Neste] Einame i {config.NESTE_URL}")
            await page.goto(config.NESTE_URL, wait_until="load", timeout=30000)
            await debug_screenshot(page, "01_neste_landing")

            # --- COOKIE HANDLING ---
            await close_cookies(page)
            await debug_screenshot(page, "02_neste_after_cookies")

            # Bandome rasti ir paspausti "Prisijungti" (tik matoma nuoroda)
            prisijungti = page.locator('a:visible:has-text("Prisijungti"), button:visible:has-text("Prisijungti")')
            count = await prisijungti.count()
            print(f"[Neste] Rasta matomu 'Prisijungti' elementu: {count}")

            if count > 0:
                # Nuoroda gali tureti target="_blank" — naviguojam tiesiogiai
                href = await prisijungti.first.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        href = "https://www.neste.lt" + href
                    print(f"[Neste] Einame i login: {href}")
                    await page.goto(href, wait_until="load", timeout=20000)
                else:
                    await prisijungti.first.click()
                    await page.wait_for_load_state("load", timeout=20000)
                await debug_screenshot(page, "03_neste_login_form")

                # --- 1 zingsnis: Vartotojo vardas (identifier) ---
                identifier_input = page.locator('input[name="identifier"], input[type="email"], input[type="text"]:visible')
                id_count = await identifier_input.count()
                print(f"[Neste] Identifier lauku rasta: {id_count}")

                if id_count > 0:
                    await identifier_input.first.fill(config.NESTE_EMAIL)
                    print(f"[Neste] Identifier ivestas")

                    # Spaudziam "Testi" / Submit (Neste naudoja custom <sty-button>)
                    testi_btn = page.locator('[type="submit"]:visible')
                    if await testi_btn.count() > 0:
                        await testi_btn.first.click()
                        print("[Neste] Paspaustas Submit/Testi")
                        await page.wait_for_load_state("load", timeout=10000)
                        await page.wait_for_timeout(500)

                await debug_screenshot(page, "04_neste_after_identifier")

                # --- 2 zingsnis: Slaptazodis ---
                pwd_input = page.locator('input[type="password"]:visible')
                try:
                    await pwd_input.wait_for(timeout=8000)
                    print("[Neste] Password laukas atsirado")
                except Exception:
                    print("[Neste] Password laukas nepasirade per 10s")

                pwd_count = await pwd_input.count()
                print(f"[Neste] Password lauku rasta: {pwd_count}")
                if pwd_count > 0:
                    await pwd_input.first.fill(config.NESTE_PASSWORD)

                    await debug_screenshot(page, "05_neste_credentials_filled")

                    # Submit password (Neste naudoja custom <sty-button>)
                    submit = page.locator('[type="submit"]:visible')
                    submit_count = await submit.count()
                    print(f"[Neste] Submit mygtuku: {submit_count}")
                    if submit_count > 0:
                        await submit.first.click()
                        await page.wait_for_load_state("load", timeout=20000)

                await debug_screenshot(page, "05_neste_after_login")
                print(f"[Neste] URL po prisijungimo: {page.url}")

                # ── 2FA aptikimas ──────────────────────────────────────────
                await page.wait_for_timeout(500)
                _twofa_found = False
                for _twofa_sel in [
                    'input[name="code"]',
                    'input[placeholder*="kod" i]',
                    '[class*="twofactor" i]',
                    '[class*="two-factor" i]',
                    'text=dviejų faktorių',
                    'text=Verification',
                    'text=verification code',
                    'text=Two-factor',
                ]:
                    if await page.locator(_twofa_sel).count() > 0:
                        _twofa_found = True
                        break

                if _twofa_found:
                    print("[Neste] 2FA patvirtinimo kodas praSomas.")
                    print("[Neste] Cookies pasenE (~60-90 dienu ciklas).")
                    print("[Neste] Paleiskite lokaliai: python export_neste_cookies.py")
                    await debug_screenshot(page, "2fa_screen")
                    return results  # Grazinami tustys rezultatai, nesaugome cookies
                # ──────────────────────────────────────────────────────────

            # -- 2. Navigacija: tiesiai i "Sutarties kainos ir nuolaidos" --
            await page.wait_for_timeout(800)
            await debug_screenshot(page, "06_neste_looking_for_menu")

            PRICE_URL = "https://www.neste.lt/lt/price-reports/card"
            print(f"[Neste] Einame tiesiai i: {PRICE_URL}")
            await page.goto(PRICE_URL, wait_until="load", timeout=30000)
            await page.wait_for_timeout(2000)
            await debug_screenshot(page, "07_neste_price_reports")

            page_head = (await page.inner_text("body"))[:800].replace("\n", " | ")
            print(f"[Neste] Puslapio pradzia: {page_head}")

            # -- 2b. Uzpildome ataskaitos forma (klientas, salis, data) --
            # Neste naudoja Chosen.js: native <select> pasleptas, todel
            # reiksmes nustatomos per JS + 'change'/'chosen:updated' eventus.
            try:
                async def js_select(sel_loc, value, multiple=False):
                    script = """(el, val) => {
                        if (el.multiple) {
                            Array.from(el.options).forEach(o => o.selected = (o.value === val));
                        } else {
                            el.value = val;
                        }
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        if (window.jQuery) {
                            window.jQuery(el).trigger('chosen:updated');
                            window.jQuery(el).trigger('change');
                        }
                    }"""
                    await sel_loc.evaluate(script, value)

                selects = page.locator("select")
                sel_count = await selects.count()
                print(f"[Neste] Select lauku: {sel_count}")
                for i in range(sel_count):
                    sel = selects.nth(i)
                    meta = await sel.evaluate(
                        "el => ({id: el.id, name: el.name, multiple: el.multiple, opts: Array.from(el.options).map(o => [o.value, o.text.trim()])})"
                    )
                    print(f"[Neste] Select #{i} ({meta['name']}, multiple={meta['multiple']}): {meta['opts'][:12]}")
                    texts = [t for v, t in meta["opts"]]
                    low = [t.lower() for t in texts]

                    if any("delamode" in t for t in low):
                        val = next(v for v, t in meta["opts"] if "delamode" in t.lower())
                        await js_select(sel, val)
                        print(f"[Neste] Klientas pasirinktas (value={val})")
                    elif any("lietuva" in t for t in low):
                        val = next((v for v, t in meta["opts"] if t.strip().lower() == "lietuva"), None)
                        if val is None:
                            val = next(v for v, t in meta["opts"] if "lietuva" in t.lower())
                        await js_select(sel, val)
                        print(f"[Neste] Salis pasirinkta (value={val})")

                await page.wait_for_timeout(800)

                inputs_meta = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('input')).filter(i => i.type !== 'hidden').map(i => [i.type, i.id, i.name])"
                )
                print(f"[Neste] Inputai: {inputs_meta}")

                dd, mm, yy = report_date.split(".")
                date_el = None
                for tp, iid, nm in inputs_meta:
                    if "dat" in ((iid or "") + (nm or "")).lower() and iid:
                        date_el = page.locator(f"#{iid}")
                        break
                if date_el is None:
                    dl = page.locator("input[type='date']")
                    if await dl.count() > 0:
                        date_el = dl.first
                if date_el is not None:
                    dtype = await date_el.evaluate("el => el.type")
                    dval = f"{yy}-{mm}-{dd}" if dtype == "date" else report_date
                    await date_el.evaluate(
                        """(el, val) => {
                            el.value = val;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        dval,
                    )
                    print(f"[Neste] Data nustatyta: {dval} (type={dtype})")
                else:
                    print("[Neste] Datos laukas nerastas")

                await page.wait_for_timeout(500)

                # Ataskaita BE PVM (pagal originalu Egles dizaina)
                vat_cb = page.locator("#edit-include-vat")
                if await vat_cb.count() > 0:
                    if await vat_cb.evaluate("el => el.checked"):
                        await vat_cb.evaluate("el => { el.checked = false; el.dispatchEvent(new Event('change', {bubbles: true})); }")
                        print("[Neste] 'Rodyti kainas su PVM' isjungta — ataskaita be PVM")
                    else:
                        print("[Neste] PVM varnele jau isjungta — ataskaita be PVM")

                submit_btn = page.locator('form:has(#edit-price-date) input[type="submit"], form:has(#edit-price-date) button[type="submit"], #edit-submit--2')
                sb = await submit_btn.count()
                print(f"[Neste] Submit kandidatu: {sb}")
                if sb > 0:
                    try:
                        await submit_btn.first.click(timeout=5000)
                    except Exception:
                        await submit_btn.first.evaluate("el => el.click()")
                    print("[Neste] Paspausta 'Issaugoti pasirinkimus'")
                    await page.wait_for_timeout(6000)
                    await debug_screenshot(page, "09_neste_report")
                    rep_head = (await page.inner_text("body"))[:700].replace("\n", " | ")
                    print(f"[Neste] Po formos: {rep_head}")
            except Exception as fe:
                print(f"[Neste] Formos pildymo klaida: {fe}")

            await debug_screenshot(page, "08_neste_final_state")
            print(f"[Neste] Galutinis URL: {page.url}")

            # -- 3. Bandome rasti kainas puslapyje --
            page_text = await page.inner_text("body")

            if "Diesel Futura" in page_text or "diesel" in page_text.lower():
                print("[Neste] Rastas 'Diesel' tekste!")
                rows = await page.query_selector_all("tr")
                for row in rows:
                    text = await row.inner_text()
                    if "Diesel Futura" in text:
                        cells = await row.query_selector_all("td")
                        for cell in cells:
                            cell_text = (await cell.inner_text()).strip().replace(",", ".")
                            try:
                                val = float(cell_text)
                                if 0.5 < val < 3.0:
                                    results["diesel"] = val
                                    print(f"[Neste] Diesel Futura: {val} EUR/l")
                                    break
                            except ValueError:
                                continue

                    if "AdBlue" in text:
                        cells = await row.query_selector_all("td")
                        for cell in cells:
                            cell_text = (await cell.inner_text()).strip().replace(",", ".")
                            try:
                                val = float(cell_text)
                                if 0.1 < val < 3.0:
                                    results["adblue"] = val
                                    print(f"[Neste] AdBlue: {val} EUR/l")
                                    break
                            except ValueError:
                                continue

            # ── Cookies issaugojimas ───────────────────────────────────
            # Saugome TIK kai sesija tikrai veike (rastos kainos) — kitaip
            # perrasytume geras cookies neprisijungusios sesijos duomenimis.
            if results["diesel"] is None and results["adblue"] is None:
                print("[Neste] Kainos nerastos — cookies neissaugomos (kad neperrasytu geru).")
            else:
                try:
                    os.makedirs(os.path.dirname(config.NESTE_COOKIES), exist_ok=True)
                    await context.storage_state(path=config.NESTE_COOKIES)
                    print(f"[Neste] Cookies issaugoti: {config.NESTE_COOKIES}")

                    with open(config.NESTE_COOKIES, "r", encoding="utf-8") as _f:
                        _storage_json = _f.read()
                    from as24_relogin import update_github_secret
                    update_github_secret(_storage_json, secret_name="NESTE_STORAGE_STATE")
                except Exception as _ce:
                    print(f"[Neste] Cookie save klaida (nekritine): {_ce}")
            # ──────────────────────────────────────────────────────────

            # Istiname laikina faila jei buvo sukurtas
            if _tmp_file:
                try:
                    os.unlink(_tmp_file)
                except Exception:
                    pass

            date_parts = report_date.split(".")
            results["date"] = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"

        except Exception as e:
            print(f"[Neste] Klaida: {e}")
            await debug_screenshot(page, "99_neste_error")
            raise
        finally:
            await browser.close()

    return results


async def scrape_neste():
    """Apvalkalas su 120s timeout — neleis uzstrigti."""
    import asyncio
    try:
        return await asyncio.wait_for(_scrape_neste_impl(), timeout=120)
    except asyncio.TimeoutError:
        print("[Neste] !! Timeout (120s) — scraperis uzstrigo, grazinami tustys rezultatai")
        return {"diesel": None, "adblue": None, "date": None}


def run_neste_scraper():
    import asyncio
    return asyncio.run(scrape_neste())


if __name__ == "__main__":
    print("=== Neste Scraper testas ===")
    try:
        result = run_neste_scraper()
        print(f"Rezultatas: {result}")
    except Exception as e:
        print(f"Klaida: {e}")

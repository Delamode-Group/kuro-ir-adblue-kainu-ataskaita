"""
Neste cookies eksportavimas su automatišku 2FA valdymu.

Veikimo eiga:
  1. Atidaro Chrome su prisijungimo langu
  2. Automatiškai suveda el. paštą ir slaptažodį
  3. Jei Neste prašo patvirtinimo kodo:
       - Programa pati paspaudžia "siųsti kodą el. paštu"
       - Parodo langą su kodo įvedimo lauku
       - Vartotojas įveda gautą kodą → programa suveda į svetainę
  4. Saugo cookies į cookies/neste_cookies.json (~60-90 dienų galiojimas)

Paleidžiama kas ~60-90 dienų kai GitHub Actions praneša, kad 2FA prašomas.
"""
import asyncio
import json
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright
import config

OUTPUT_FILE = config.NESTE_COOKIES   # "cookies/neste_cookies.json"
LOGIN_URL   = "https://www.neste.lt/lt"


# ─── Tkinter: kodo įvedimo dialogas ──────────────────────────────────────────


def _ask_for_code(email_hint: str = "") -> str | None:
    """
    Parodo langą su kodo įvedimo lauku.
    Gražina įvestą kodą arba None jei atšaukta.
    """
    try:
        import tkinter as tk
    except ImportError:
        # Fallback: konsolinis įvedimas
        val = input(f"[Neste] Iveskite 2FA koda (siustas i {email_hint}): ").strip()
        return val or None

    result = {"code": None}

    root = tk.Tk()
    root.title("Neste — Patvirtinimo kodas")
    root.resizable(False, False)

    w, h = 430, 230
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.configure(bg="#f2f2f2")

    # ── Antraštė ──
    hdr = tk.Frame(root, bg="#2980b9", padx=16, pady=10)
    hdr.pack(fill="x")
    tk.Label(
        hdr,
        text="\U0001f4e7  Neste patvirtinimo kodas",
        font=("Segoe UI", 11, "bold"),
        fg="white", bg="#2980b9", anchor="w",
    ).pack(fill="x")

    # ── Turinys ──
    body = tk.Frame(root, bg="#f2f2f2", padx=22, pady=14)
    body.pack(fill="both", expand=True)

    dest = email_hint or config.NESTE_EMAIL or "jūsų el. paštą"
    tk.Label(
        body,
        text=f"Kodas išsiųstas į:  {dest}",
        font=("Segoe UI", 9), bg="#f2f2f2", fg="#555555",
    ).pack(anchor="w")

    tk.Label(
        body,
        text="Įveskite gautą kodą:",
        font=("Segoe UI", 9, "bold"), bg="#f2f2f2", fg="#1a1a1a",
        pady=10,
    ).pack(anchor="w")

    code_var = tk.StringVar()
    entry = tk.Entry(
        body,
        textvariable=code_var,
        font=("Courier New", 18, "bold"),
        width=11, justify="center",
        relief="solid", bd=1,
    )
    entry.pack()
    entry.focus_set()

    def _confirm(*_):
        v = code_var.get().strip()
        if v:
            result["code"] = v
            root.destroy()

    def _cancel(*_):
        root.destroy()

    # ── Mygtukai ──
    btn_row = tk.Frame(root, bg="#f2f2f2", pady=10)
    btn_row.pack(fill="x")

    tk.Button(
        btn_row, text="   Patvirtinti   ",
        font=("Segoe UI", 10, "bold"),
        bg="#27ae60", fg="white",
        activebackground="#219a52", activeforeground="white",
        relief="flat", padx=14, pady=7, cursor="hand2",
        command=_confirm,
    ).pack(side="right", padx=(6, 22))

    tk.Button(
        btn_row, text="   Atšaukti   ",
        font=("Segoe UI", 10),
        bg="#95a5a6", fg="white",
        activebackground="#7f8c8d", activeforeground="white",
        relief="flat", padx=14, pady=7, cursor="hand2",
        command=_cancel,
    ).pack(side="right", padx=4)

    entry.bind("<Return>", _confirm)
    root.protocol("WM_DELETE_WINDOW", _cancel)
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.mainloop()

    return result["code"]


# ─── Pagalbinės Playwright funkcijos ─────────────────────────────────────────


async def _close_cookie_popup(page):
    """Automatiškai uždaro cookie consent popup."""
    try:
        await page.wait_for_selector(
            "#onetrust-banner-sdk, #onetrust-consent-sdk, "
            "[class*='cookie'], [id*='cookie'], [class*='consent']",
            timeout=5000,
        )
    except Exception:
        pass

    for sel in ["#onetrust-accept-btn-handler", "#onetrust-reject-all-handler",
                ".onetrust-close-btn-handler"]:
        btn = page.locator(sel)
        if await btn.count() > 0:
            await btn.first.click(force=True)
            print("[Neste] Cookie popup uzdarytas (OneTrust).")
            await page.wait_for_timeout(800)
            return

    for text in ["Necessary cookies only", "Reject All", "Accept All",
                 "Priimti visus", "Sutinku", "Priimti"]:
        btn = page.locator(f'button:has-text("{text}")')
        if await btn.count() > 0:
            await btn.first.click(force=True)
            print(f"[Neste] Cookie popup uzdarytas: '{text}'")
            await page.wait_for_timeout(800)
            return


async def _try_send_code_by_email(page):
    """
    Bando paspausti 'siųsti kodą el. paštu' mygtuką.
    Gražina True jei sėkmingai paspaudė.
    """
    candidates = [
        # Tekstiniai variantai (LT ir EN)
        'button:has-text("el. pašt")',
        'button:has-text("siusti")',
        'button:has-text("Email")',
        'button:has-text("email")',
        'button:has-text("e-mail")',
        'a:has-text("el. pašt")',
        'a:has-text("Email")',
        '[class*="email" i] button',
        '[class*="email" i] a',
        # Ikonos / data atributai
        '[data-method="email"]',
        '[data-channel="email"]',
    ]
    for sel in candidates:
        btn = page.locator(sel)
        if await btn.count() > 0:
            await btn.first.click()
            print(f"[Neste] 'Siusti koda el. pastu' paspaustas: {sel}")
            await page.wait_for_timeout(2000)
            return True
    return False


async def _extract_email_hint(page) -> str:
    """Bando rasti el. pašto adresą 2FA puslapyje."""
    try:
        text = await page.inner_text("body")
        m = re.search(r"[\w.+%-]+@[\w.-]+\.\w{2,}", text)
        if m:
            return m.group()
    except Exception:
        pass
    return ""


async def _handle_2fa(page) -> bool:
    """
    Pilnas 2FA apdorojimas:
      1. Bando siųsti kodą el. paštu
      2. Rodo kodo įvedimo langą
      3. Suveda kodą ir patvirtina
    Gražina True jei sėkmingai įvykdyta.
    """
    print("[Neste] 2FA ekranas aptiktas — siunčiame kodą el. paštu...")

    # Siunčiame kodą
    sent = await _try_send_code_by_email(page)
    if not sent:
        print("[Neste] Automatinis siuntimo mygtukas nerastas — gali būti siųsta automatiškai.")

    # El. paštas iš puslapio
    email_hint = await _extract_email_hint(page)
    if email_hint:
        print(f"[Neste] Kodas siuncamas i: {email_hint}")

    # Kodo įvedimo dialogas (sinchroniškas, tkinter)
    code = await asyncio.to_thread(_ask_for_code, email_hint)

    if not code:
        print("[Neste] Kodas neiveistas — nutraukiame.")
        return False

    print(f"[Neste] Kodas gautas ({len(code)} simboliai) — vedam i lauką...")

    # Suvedam kodą į puslapį
    code_field_selectors = [
        'input[name="code"]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[placeholder*="kod" i]',
        'input[placeholder*="verif" i]',
        'input[maxlength="6"]',
        'input[maxlength="8"]',
        'input[type="number"]',
        'input[type="text"]:visible',
    ]
    filled = False
    for sel in code_field_selectors:
        field = page.locator(sel).first
        if await field.count() > 0:
            try:
                await field.fill(code)
                print(f"[Neste] Kodas suvestas ({sel}).")
                filled = True
                break
            except Exception:
                continue

    if not filled:
        print("[Neste] Kodo lauko nepavyko rasti — bandome klaviatūra.")
        await page.keyboard.type(code)

    # Patvirtinti
    await page.wait_for_timeout(500)
    for submit_sel in ['[type="submit"]:visible', 'button:visible:has-text("Prisijungti")',
                       'button:visible:has-text("Patvirtinti")', 'button:visible:has-text("Verify")']:
        btn = page.locator(submit_sel)
        if await btn.count() > 0:
            await btn.first.click()
            print("[Neste] Kodo patvirtinimas pateiktas.")
            await page.wait_for_load_state("networkidle", timeout=30000)
            break

    return True


# ─── GitHub Secret atnaujinimas ───────────────────────────────────────────────


def _update_neste_secret(storage_json_str: str) -> bool:
    """Atnaujina NESTE_STORAGE_STATE GitHub Secret. Naudoja as24_relogin logiką."""
    try:
        from as24_relogin import update_github_secret as _upd
        return _upd(storage_json_str, secret_name="NESTE_STORAGE_STATE")
    except Exception as e:
        print(f"[Neste] Secret update klaida: {e}")
        return False


# ─── Pagrindinis prisijungimo srautas ─────────────────────────────────────────


async def main():
    print("=" * 60)
    print("Neste cookies eksportavimas")
    print("=" * 60)
    print()

    email    = config.NESTE_EMAIL
    password = config.NESTE_PASSWORD

    auto_fill = bool(email and password)
    if auto_fill:
        print("Rezimas: automatinis (el. pastas + slaptazodis suvedami automatiskai)")
    else:
        print("Rezimas: rankinis (NESTE_EMAIL / NESTE_PASSWORD nenustatyti)")
        print("Prisijunkite rankiniu budu atsidariusiame narstukles lange.")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # ── 1. Ateiname į Neste ──
        print(f"Einame i: {LOGIN_URL}")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)
        await _close_cookie_popup(page)

        # ── 2. Randame ir spaudžiame "Prisijungti" ──
        prisijungti = page.locator(
            'a:visible:has-text("Prisijungti"), button:visible:has-text("Prisijungti")'
        )
        if await prisijungti.count() > 0:
            href = await prisijungti.first.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.neste.lt" + href
                await page.goto(href, wait_until="networkidle", timeout=30000)
            else:
                await prisijungti.first.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
        await page.wait_for_timeout(1000)

        if auto_fill:
            # ── 3. El. paštas ──
            email_input = page.locator(
                'input[name="identifier"], input[type="email"], '
                'input[placeholder*="email" i], input[placeholder*="naudotojo" i], '
                'input[type="text"]:visible'
            ).first
            try:
                await email_input.wait_for(state="visible", timeout=10000)
                await email_input.fill(email)
                print("El. pastas suvestas.")
            except Exception as e:
                print(f"El. pasto laukas nerastas: {e}")

            # Toliau / Next
            await page.wait_for_timeout(400)
            for sub_sel in ['[type="submit"]:visible', 'input[type="submit"]:visible']:
                btn = page.locator(sub_sel)
                if await btn.count() > 0:
                    pwd_visible = page.locator('input[type="password"]:visible')
                    if await pwd_visible.count() == 0:
                        await btn.first.click()
                        await page.wait_for_timeout(2000)
                    break

            # ── 4. Slaptažodis ──
            pwd_input = page.locator('input[type="password"]:visible').first
            try:
                await pwd_input.wait_for(state="visible", timeout=12000)
                await pwd_input.fill(password)
                print("Slaptazodis suvestas.")
            except Exception as e:
                print(f"Slaptazodzio laukas nerastas: {e}")

            # Overlay
            await page.evaluate("""
            () => {
                const d = document.createElement('div');
                d.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#27ae60;color:#fff;' +
                    'padding:12px 18px;border-radius:8px;font:bold 13px Segoe UI,Arial,sans-serif;' +
                    'z-index:2147483647;box-shadow:0 4px 12px rgba(0,0,0,.3);pointer-events:none';
                d.textContent = '✓ Duomenys suvesti — spauskite Prisijungti';
                document.body.appendChild(d);
            }
            """)

            # Submit slaptažodžio
            for sub_sel in ['[type="submit"]:visible', 'button:visible:has-text("Prisijungti")']:
                btn = page.locator(sub_sel)
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    break
        else:
            # Rankinis rezimas — tik pranešimas narstuklejes lange
            await page.evaluate("""
            () => {
                const d = document.createElement('div');
                d.style.cssText = 'position:fixed;top:10px;right:10px;background:#2980b9;color:#fff;' +
                    'padding:12px 18px;border-radius:8px;font:bold 13px Segoe UI,Arial,sans-serif;' +
                    'z-index:2147483647;box-shadow:0 4px 12px rgba(0,0,0,.3);pointer-events:none';
                d.textContent = 'Prisijunkite rankiniu būdu — cookies bus issaugotos automatiskai';
                document.body.appendChild(d);
            }
            """)
            print("Laukiame rankinio prisijungimo ...")

        if auto_fill:
            # ── 5. Tikriname ar yra 2FA (tik auto-fill rezime) ──
            await page.wait_for_timeout(2500)

            _2fa_selectors = [
                'input[name="code"]',
                'input[autocomplete="one-time-code"]',
                'input[inputmode="numeric"]',
                'input[maxlength="6"]',
                'input[placeholder*="kod" i]',
                'input[placeholder*="verif" i]',
                'text=patvirtinimo kod',
                'text=verification code',
                'text=Two-factor',
                'text=dviejų faktorių',
            ]
            twofa_detected = any(
                await page.locator(sel).count() > 0 for sel in _2fa_selectors
            )

            if twofa_detected:
                ok = await _handle_2fa(page)
                if not ok:
                    print("Nutraukiame — cookies neissaugomi.")
                    await browser.close()
                    return
                await page.wait_for_timeout(2000)
            else:
                print("2FA nepraSomas.")

        # ── 6. Laukiame kol atsidursime ne login.neste.com ──
        print("Laukiame prisijungimo (iki 5 min.) ...")
        try:
            await page.wait_for_function(
                "() => !window.location.hostname.includes('login.neste.com')",
                timeout=300000,
            )
            print(f"Prisijungimas sekminas! URL: {page.url}")
        except Exception:
            print(f"Timeout — dabartinis URL: {page.url}")
            print("Bandome issaugoti cookies bet kokiu atveju.")

        await page.wait_for_timeout(2000)

        # ── 7. Saugome cookies ──
        storage = await context.storage_state()
        cookies_count = len(storage.get("cookies", []))

        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(storage, f, ensure_ascii=False, indent=2)

        # GitHub Secret atnaujinimas
        _gh_ok = _update_neste_secret(json.dumps(storage, ensure_ascii=False))

        print()
        print("=" * 60)
        print(f"Cookies issaugoti : {OUTPUT_FILE}")
        print(f"Cookies kiekis    : {cookies_count}")
        if _gh_ok:
            print("GitHub Secret     : NESTE_STORAGE_STATE atnaujintas")
        else:
            print("GitHub Secret     : neatnaujintas (nenustatytas GITHUB_TOKEN/GITHUB_REPO)")
        print("Kita 2FA uzklausas tikimetis po ~60-90 dienu.")
        print("=" * 60)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

"""
AS24 sesijos atsinaujinimas.
Kai cookies pasibaigia ir API grazina 400/401, sis modulis:
  1. Parodo tkinter langa su informacija apie pasibaigusia sesija.
  2. Jei vartotojas spaudzia "Prisijungti" — atveria Chrome su issuzpildytais duomenimis.
  3. Vartotojui paspaudus "Prisijungti" AS24 svetaineje — eksportuoja naujas cookies.
  4. Issaugo as24_storage.json ir (jei yra token) atnaujina GitHub Secret.
"""
import asyncio
import base64
import hashlib
import json
import os
import struct
import sys

import requests
from playwright.async_api import async_playwright
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.poly1305 import Poly1305
from cryptography.hazmat.primitives import serialization

import config

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


AS24_LOGIN_URL = "https://extranet.as24.com/extranet/lt/login"
AS24_HOME_PATTERN = "/extranet/lt/home"


# ─── NaCl sealed box (GitHub Secret sifrAvimas) ───────────────────────────────


def _rotl32(v, n):
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _u32(x):
    return x & 0xFFFFFFFF


_SIG = struct.unpack("<4I", b"expand 32-byte k")


def _do_rounds(z):
    """10 double-rounds in-place — HSalsa20 nesumuoja originalios busenos."""
    for _ in range(10):
        z[ 4] ^= _rotl32(_u32(z[ 0] + z[12]),  7)
        z[ 8] ^= _rotl32(_u32(z[ 4] + z[ 0]),  9)
        z[12] ^= _rotl32(_u32(z[ 8] + z[ 4]), 13)
        z[ 0] ^= _rotl32(_u32(z[12] + z[ 8]), 18)
        z[ 9] ^= _rotl32(_u32(z[ 5] + z[ 1]),  7)
        z[13] ^= _rotl32(_u32(z[ 9] + z[ 5]),  9)
        z[ 1] ^= _rotl32(_u32(z[13] + z[ 9]), 13)
        z[ 5] ^= _rotl32(_u32(z[ 1] + z[13]), 18)
        z[14] ^= _rotl32(_u32(z[10] + z[ 6]),  7)
        z[ 2] ^= _rotl32(_u32(z[14] + z[10]),  9)
        z[ 6] ^= _rotl32(_u32(z[ 2] + z[14]), 13)
        z[10] ^= _rotl32(_u32(z[ 6] + z[ 2]), 18)
        z[ 3] ^= _rotl32(_u32(z[15] + z[11]),  7)
        z[ 7] ^= _rotl32(_u32(z[ 3] + z[15]),  9)
        z[11] ^= _rotl32(_u32(z[ 7] + z[ 3]), 13)
        z[15] ^= _rotl32(_u32(z[11] + z[ 7]), 18)
        z[ 1] ^= _rotl32(_u32(z[ 0] + z[ 3]),  7)
        z[ 2] ^= _rotl32(_u32(z[ 1] + z[ 0]),  9)
        z[ 3] ^= _rotl32(_u32(z[ 2] + z[ 1]), 13)
        z[ 0] ^= _rotl32(_u32(z[ 3] + z[ 2]), 18)
        z[ 6] ^= _rotl32(_u32(z[ 5] + z[ 4]),  7)
        z[ 7] ^= _rotl32(_u32(z[ 6] + z[ 5]),  9)
        z[ 4] ^= _rotl32(_u32(z[ 7] + z[ 6]), 13)
        z[ 5] ^= _rotl32(_u32(z[ 4] + z[ 7]), 18)
        z[11] ^= _rotl32(_u32(z[10] + z[ 9]),  7)
        z[ 8] ^= _rotl32(_u32(z[11] + z[10]),  9)
        z[ 9] ^= _rotl32(_u32(z[ 8] + z[11]), 13)
        z[10] ^= _rotl32(_u32(z[ 9] + z[ 8]), 18)
        z[12] ^= _rotl32(_u32(z[15] + z[14]),  7)
        z[13] ^= _rotl32(_u32(z[12] + z[15]),  9)
        z[14] ^= _rotl32(_u32(z[13] + z[12]), 13)
        z[15] ^= _rotl32(_u32(z[14] + z[13]), 18)


def _hsalsa20(k32, n16):
    """HSalsa20: rounds BEZ galutinio originalios busenos sudejimo."""
    k = struct.unpack("<8I", k32)
    n = struct.unpack("<4I", n16)
    z = [
        _SIG[0], k[0], k[1], k[2], k[3], _SIG[1],
        n[0], n[1], n[2], n[3],
        _SIG[2], k[4], k[5], k[6], k[7], _SIG[3],
    ]
    _do_rounds(z)
    return struct.pack("<8I", z[0], z[5], z[10], z[15], z[6], z[7], z[8], z[9])


def _salsa20_block(k32, n8, ctr):
    """Salsa20 raktinio srauto blokas SU galutine sudejimo operacija."""
    k = struct.unpack("<8I", k32)
    n = struct.unpack("<2I", n8)
    x = [
        _SIG[0], k[0], k[1], k[2], k[3], _SIG[1], n[0], n[1],
        _u32(ctr), _u32(ctr >> 32), _SIG[2], k[4], k[5], k[6], k[7], _SIG[3],
    ]
    z = list(x)
    _do_rounds(z)
    return struct.pack("<16I", *[_u32(z[i] + x[i]) for i in range(16)])


def _xsalsa20poly1305_seal(k32, n24, msg):
    subkey = _hsalsa20(k32, n24[:16])
    sn = n24[16:24]
    ks0 = _salsa20_block(subkey, sn, 0)
    poly_key = ks0[:32]
    ct = bytearray()
    # Pirmi 32 baitai msg sifrAvimas: naudojamas ks0[32:64]
    first = min(32, len(msg))
    for i in range(first):
        ct.append(msg[i] ^ ks0[32 + i])
    # Likusieji baitai: blokai 1, 2, ...
    blk, off = 1, 32
    while off < len(msg):
        ks = _salsa20_block(subkey, sn, blk)
        for b, s in zip(msg[off : off + 64], ks):
            ct.append(b ^ s)
        off += 64
        blk += 1
    p = Poly1305(poly_key)
    p.update(bytes(ct))
    return p.finalize() + bytes(ct)


def _nacl_sealed_box(recv_pub_bytes, plaintext_bytes):
    eph_priv = X25519PrivateKey.generate()
    eph_pub_bytes = eph_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    shared = eph_priv.exchange(X25519PublicKey.from_public_bytes(recv_pub_bytes))
    box_key = _hsalsa20(shared, b"\x00" * 16)
    nonce24 = hashlib.blake2b(eph_pub_bytes + recv_pub_bytes, digest_size=24).digest()
    return eph_pub_bytes + _xsalsa20poly1305_seal(box_key, nonce24, plaintext_bytes)


# ─── GitHub Secret atnaujinimas ───────────────────────────────────────────────


def update_github_secret(storage_json_str, secret_name="AS24_STORAGE_STATE"):
    """
    Atnaujina GitHub Secret su naujomis cookies (base64 encoded).
    Reikia GITHUB_TOKEN ir GITHUB_REPO aplinkos kintamuju.
    Grazina True jei sekminga.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")
    if not token or not repo:
        print(f"[Secret] GITHUB_TOKEN arba GITHUB_REPO nenurodytas — '{secret_name}' neatnaujinamas.")
        return False
    storage_b64 = base64.b64encode(storage_json_str.encode()).decode()

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Gauname GitHub viesaji rakta
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    pk_data = resp.json()
    key_id = pk_data["key_id"]
    pub_bytes = base64.b64decode(pk_data["key"])

    # Uzsifreuojame
    encrypted = _nacl_sealed_box(pub_bytes, storage_b64.encode())
    encrypted_b64 = base64.b64encode(encrypted).decode()

    # Atnaujinamas Secret
    resp = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted_b64, "key_id": key_id},
        timeout=15,
    )
    code = resp.status_code
    if code in (201, 204):
        print(f"[Secret] '{secret_name}' atnaujintas (HTTP {code}).")
        return True
    else:
        print(f"[Secret] '{secret_name}' klaida: HTTP {code} — {resp.text[:200]}")
        return False


# ─── Tkinter dialogas ─────────────────────────────────────────────────────────


def show_relogin_dialog():
    """
    Parodo langeli informuojanti apie pasibajusia sesija.
    Grazina True jei vartotojas spaudzia "Prisijungti", False jei "Atsaukti".
    """
    try:
        import tkinter as tk
    except ImportError:
        print("[AS24] tkinter neprieinamas — negalima rodyti dialogo.")
        return False

    result = {"choice": False}

    root = tk.Tk()
    root.title("AS24 — Sesijos klaida")
    root.resizable(False, False)

    # Centruojame
    w, h = 540, 310
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.configure(bg="#f2f2f2")

    # ── Antraste (raudona juosta) ──
    hdr = tk.Frame(root, bg="#c0392b", padx=16, pady=10)
    hdr.pack(fill="x")
    tk.Label(
        hdr,
        text="⚠  AS24 portalo sesija pasibaigė",
        font=("Segoe UI", 11, "bold"),
        fg="white",
        bg="#c0392b",
        anchor="w",
    ).pack(fill="x")

    # ── Informacines eilutes ──
    info_frame = tk.Frame(root, bg="#f2f2f2", padx=18, pady=12)
    info_frame.pack(fill="both", expand=True)

    rows_data = [
        ("Situacija:",    "AS24 portalo cookies sesija pasibaigė"),
        ("Klaida:",       "HTTP 400 — autentifikacija nepavyko"),
        ("Veiksmas:",     "Būtina prisijungti prie AS24 portalo iš naujo"),
        ("Instrukcija:",  "Atsidarys narsėklė su jau suvestais duomenimis.\n"
                          "Tiesiog paspauskite mygtuką „Prisijungti“ svetainėje."),
    ]

    for i, (lbl, val) in enumerate(rows_data):
        bg = "#ffffff" if i % 2 == 0 else "#eaeaea"
        row = tk.Frame(info_frame, bg=bg, pady=5, padx=10)
        row.pack(fill="x", pady=1)
        tk.Label(
            row, text=lbl,
            font=("Segoe UI", 9, "bold"),
            width=13, anchor="nw",
            bg=bg, fg="#555555",
        ).pack(side="left", anchor="nw")
        tk.Label(
            row, text=val,
            font=("Segoe UI", 9),
            anchor="nw", justify="left",
            bg=bg, fg="#1a1a1a",
            wraplength=370,
        ).pack(side="left", anchor="nw", fill="x", expand=True)

    # ── Mygtuku zona ──
    btn_frame = tk.Frame(root, bg="#f2f2f2", pady=10)
    btn_frame.pack(fill="x")

    def on_login():
        result["choice"] = True
        root.destroy()

    def on_cancel():
        result["choice"] = False
        root.destroy()

    tk.Button(
        btn_frame,
        text="   Prisijungti   ",
        font=("Segoe UI", 10, "bold"),
        bg="#27ae60", fg="white",
        activebackground="#219a52", activeforeground="white",
        relief="flat", padx=14, pady=7,
        cursor="hand2",
        command=on_login,
    ).pack(side="right", padx=(6, 22))

    tk.Button(
        btn_frame,
        text="   Atšaukti   ",
        font=("Segoe UI", 10),
        bg="#95a5a6", fg="white",
        activebackground="#7f8c8d", activeforeground="white",
        relief="flat", padx=14, pady=7,
        cursor="hand2",
        command=on_cancel,
    ).pack(side="right", padx=4)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.mainloop()

    return result["choice"]


# ─── Playwright: automatinis duomenu suvedimas ────────────────────────────────


async def _open_and_autofill():
    """
    Atveria Chrome, uzpildo el. pasta ir slaptazodi,
    prideda zalio overlay pranesimu, laukia vartotojo paspaudimo.
    Grazina storage_state dict arba None.
    """
    email = config.AS24_EMAIL
    password = config.AS24_PASSWORD

    if not email or not password:
        print("[AS24] AS24_EMAIL arba AS24_PASSWORD nenurodytas — auto-pildy negalimas.")
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        print(f"[AS24] Einame i: {AS24_LOGIN_URL}")
        await page.goto(AS24_LOGIN_URL, wait_until="networkidle", timeout=60000)

        # ── Cookie popup uzdarymas ──
        await page.wait_for_timeout(2000)
        for sel in [
            "#didomi-notice-agree-button",
            "button#didomi-notice-agree-button",
            '[data-testid="notice-agree-button"]',
        ]:
            btn = page.locator(sel)
            if await btn.count() > 0:
                await btn.first.click(force=True)
                print("[AS24] Cookie popup uzdarytas (selector).")
                await page.wait_for_timeout(800)
                break
        else:
            for text in ["Agree and close", "Accept all", "Accept", "Sutinku", "Pritariu"]:
                btn = page.locator(f'button:has-text("{text}"), a:has-text("{text}")')
                if await btn.count() > 0:
                    await btn.first.click(force=True)
                    print(f"[AS24] Cookie popup uzdarytas: '{text}'")
                    await page.wait_for_timeout(800)
                    break

        # ── El. pastas ──
        email_sel = (
            'input[type="email"], input[name="username"], '
            'input[name="email"], input[id*="email" i], '
            'input[placeholder*="email" i], input[placeholder*="naudotojo" i]'
        )
        email_input = page.locator(email_sel).first
        try:
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.fill(email)
            print(f"[AS24] El. pastas suvestas.")
        except Exception as e:
            print(f"[AS24] El. pasto laukas nerastas: {e}")

        # Jei reikia — spaudzia "Toliau" / "Next"
        await page.wait_for_timeout(500)
        for submit_sel in [
            'button[type="submit"]:visible',
            'input[type="submit"]:visible',
        ]:
            btn = page.locator(submit_sel)
            if await btn.count() > 0:
                # Spaudziame tik jei slaptazodzio lauko dar nematome
                pwd_now = page.locator('input[type="password"]:visible')
                if await pwd_now.count() == 0:
                    await btn.first.click()
                    print("[AS24] Paspaustas 'Toliau' mygtukas.")
                    await page.wait_for_timeout(2000)
                break

        # ── Slaptazodis ──
        pwd_input = page.locator('input[type="password"]:visible').first
        try:
            await pwd_input.wait_for(state="visible", timeout=12000)
            await pwd_input.fill(password)
            print("[AS24] Slaptazodis suvestas.")
        except Exception as e:
            print(f"[AS24] Slaptazodzio laukas nerastas: {e}")

        # ── Zalias overlay praneSimas ──
        overlay_script = """
        () => {
            if (document.getElementById('__as24_relogin_overlay__')) return;
            const d = document.createElement('div');
            d.id = '__as24_relogin_overlay__';
            d.style.cssText = [
                'position:fixed', 'bottom:24px', 'right:24px',
                'background:#27ae60', 'color:#fff',
                'padding:13px 20px', 'border-radius:8px',
                'font:bold 14px/1.4 Segoe UI,Arial,sans-serif',
                'z-index:2147483647',
                'box-shadow:0 4px 14px rgba(0,0,0,.35)',
                'pointer-events:none',
            ].join(';');
            d.innerHTML = '&#10003; Duomenys suvesti &mdash; paspauskite <u>Prisijungti</u>';
            document.body.appendChild(d);
        }
        """
        try:
            await page.evaluate(overlay_script)
            print("[AS24] Overlay praneSimas pridetas.")
        except Exception:
            pass

        # ── Laukiame vartotojo prisijungimo ──
        print("[AS24] Laukiame prisijungimo (iki 5 min.) ...")
        try:
            await page.wait_for_url(f"**{AS24_HOME_PATTERN}**", timeout=300000)
            print(f"[AS24] Prisijungimas aptiktas: {page.url}")
        except Exception:
            print("[AS24] Timeout — vartotojas neprisijunge arba uzdarejo langa.")
            await browser.close()
            return None

        await page.wait_for_timeout(2000)
        storage = await context.storage_state()
        print(f"[AS24] Cookies eksportuoti: {len(storage.get('cookies', []))} vnt.")
        await browser.close()
        return storage


def _run_playwright_login():
    """Sinchroninis wrapper async Playwright funkcijai."""
    return asyncio.run(_open_and_autofill())


# ─── Pagrindinė funkcija ──────────────────────────────────────────────────────


def run_relogin():
    """
    Pagrindine sesijos atsinaujinimo funkcija.
    Kviecia is as24_scraper.py kai gaunamas 400/401 klaidos kodas.
    Grazina True jei sesija sekmingai atnaujinta.
    """
    # CI aplinkoje negalima rodyti UI
    if os.getenv("GITHUB_ACTIONS"):
        print("[AS24] CI aplinka — relogin UI negalimas. AS24 cookies reikia atnaujinti rankiniu budu.")
        return False

    # 1. Dialogas
    print("[AS24] Rodome sesijos pasibaigimo dialoga...")
    user_confirmed = show_relogin_dialog()

    if not user_confirmed:
        print("[AS24] Vartotojas atsake prisijungti.")
        return False

    # 2. Playwright: auto-fill + eksportas
    print("[AS24] Atidarome narsykle su issuzpildytais duomenimis...")
    storage = _run_playwright_login()

    if storage is None:
        print("[AS24] Cookies negautos — prisijungimas nepavyko.")
        return False

    # 3. Issaugome lokaliuosius failus
    storage_json = json.dumps(storage, ensure_ascii=False)

    with open("as24_storage.json", "w", encoding="utf-8") as f:
        f.write(storage_json)
    print("[AS24] Cookies issaugoti: as24_storage.json")

    storage_b64 = base64.b64encode(storage_json.encode()).decode()
    with open("as24_storage_b64.txt", "w", encoding="utf-8") as f:
        f.write(storage_b64)
    print("[AS24] Base64 issaugota: as24_storage_b64.txt")

    # 4. GitHub Secret atnaujinimas (nekritinis — klaida nesustabdo)
    try:
        update_github_secret(storage_json)
    except Exception as e:
        print(f"[AS24] GitHub Secret klaida (nekritine): {e}")

    print("[AS24] Sesija sekmingai atnaujinta!")
    return True


# ─── Tiesioginis paleidimas (testas) ─────────────────────────────────────────

if __name__ == "__main__":
    print("=== AS24 relogin testas ===")
    ok = run_relogin()
    print(f"Rezultatas: {'OK' if ok else 'Nesekme / atsaukta'}")

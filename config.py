"""
Kuro kainu stebesenos sistema — nustatymai.
Visi slaptazodziai ir prisijungimai imami is aplinkos kintamuju (GitHub Secrets).
Pastaba: naudojame `os.getenv(...) or default`, kad TUSCIA env reiksme
(pvz. nenustatytas GitHub Secret) nepakeistu numatytosios.
"""
import os

# -- Orlen LT (CK) --
CK_PDF_URL = "https://www.orlenlietuva.lt/LT/Wholesale/Prices/"
CK_ADBLUE_URL = os.getenv("CK_ADBLUE_URL", "")

# -- Neste --
NESTE_URL = os.getenv("NESTE_URL") or "https://www.neste.lt/lt"
NESTE_EMAIL = os.getenv("NESTE_EMAIL", "")
NESTE_PASSWORD = os.getenv("NESTE_PASSWORD", "")
NESTE_CLIENT = "Delamode Baltics"
NESTE_COUNTRY = "Lietuva"

# -- AS24 --
AS24_URL = os.getenv("AS24_URL") or "https://extranet.as24.com/extranet/lt/home"
AS24_CLIENT_ID = os.getenv("AS24_CLIENT_ID") or "688701"
AS24_EMAIL = os.getenv("AS24_EMAIL", "")
AS24_PASSWORD = os.getenv("AS24_PASSWORD", "")

# AS24 dyzelino degalines (Kaina pagal degaline -> Suma po nuolaidu be PVM)
AS24_DIESEL_STATIONS = [
    {"name": "Gliwice",          "filter": "GLIWICE",          "country": "POL"},
    {"name": "Niederzissen A61", "filter": "NIEDERZISSEN A61", "country": "DEU"},
    {"name": "Autohof Kufstein", "filter": "AUTOHOF KUFSTEIN", "country": "AUT"},
    {"name": "Turnhout",         "filter": "TURNHOUT",         "country": "BEL"},
    {"name": "Calais Port",      "filter": "CALAIS PORT",      "country": "FRA"},
    {"name": "La Jonquera 1",    "filter": "LA JONQUERA 1",    "country": "ESP"},
    {"name": "Rodange 2",        "filter": "RODANGE",          "country": "LUX"},
    {"name": "ITA",              "filter": "ADS ANGIOINA OUEST", "country": "ITA"},
]

# AS24 AdBlue salys (Kaina pagal sali -> konkreti zona -> Suma po nuolaidu be PVM)
AS24_ADBLUE_COUNTRIES = [
    {"name": "Liuksemburgas", "code": "LUX", "country_filter": "Liuksemburgas", "zone": "A"},
    {"name": "Cekija",        "code": "CZE", "country_filter": "Cekija",        "zone": "A"},
    {"name": "Prancuzija",    "code": "FRA", "country_filter": "Prancuzija",    "zone": "D"},
    {"name": "Belgija",       "code": "BEL", "country_filter": "Belgija",       "zone": "A"},
    {"name": "Italija",       "code": "ITA", "country_filter": "Italija",       "zone": "A"},
    {"name": "Vokietija",     "code": "DEU", "country_filter": "Vokietija",     "zone": ""},
    {"name": "Lenkija",       "code": "POL", "country_filter": "Lenkija",       "zone": "A"},
    {"name": "Austrija",      "code": "AUT", "country_filter": "Austrija",      "zone": "A"},
    {"name": "Ispanija",      "code": "ESP", "country_filter": "Ispanija",      "zone": "A"},
    {"name": "Slovenija",     "code": "SVN", "country_filter": "Slovenija",     "zone": "A"},
    {"name": "Nyderlandai",   "code": "NLD", "country_filter": "Nyderlandai",   "zone": ""},
]

# -- JSON failas --
JSON_FILE = "kuro_kainos.json"

# -- Cookies --
COOKIES_DIR = "cookies"
AS24_COOKIES = os.path.join(COOKIES_DIR, "as24_cookies.json")
NESTE_COOKIES = os.path.join(COOKIES_DIR, "neste_cookies.json")

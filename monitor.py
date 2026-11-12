
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import requests

# Configuration Bookingkit
BASE_URL = "https://eu5.bookingkit.de/onPage/detail"

CUSTOMER_ID = "8c1c1aeae7ca0f5858830e58e74dbdac"
CONFIG_VERSION = "6d81d7b80883101123aa3b5c62b5b749"

ORIGINAL_PAGE = (
    "https://www.european-flight-academy.com/"
    "en/registration-dlr-test"
    "#anchor_com_liferay_journal_content_web_portlet_"
    "JournalContentPortlet_INSTANCE_mirgwebcontent"
)

EVENTS = {
    "DLR Hamburg - Allemand": "953b11ef0dce1a9079e1e93896d15578",
    "DLR Hamburg - Anglais/Allemand": "31e09ad84f2ba0f4a931847f15235d44",
}

STATE_FILE = Path("state.json")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_bookingkit_html(event_id):
    # Le paramètre url doit être doublement encodé dans la requête finale.
    encoded_page = quote(ORIGINAL_PAGE, safe="")

    params = {
        "e": event_id,
        "cw": CUSTOMER_ID,
        "targetId": "bookingKitContainer",
        "browserlang": "fr-FR",
        "url": encoded_page,
        "v": CONFIG_VERSION,
        "lang": "en",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BookingKitMonitor/1.0)"
        ),
        "Accept": "*/*",
        "Referer": (
            "https://www.european-flight-academy.com/"
            "en/registration-dlr-test"
        ),
    }

    response = requests.get(
        BASE_URL,
        params=params,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    body = response.text.strip()

    # Bookingkit peut renvoyer un fragment JS :
    # BookingKitApp.insertBkContent({"html":"..."})
    marker = "BookingKitApp.insertBkContent("

    if marker in body:
        start = body.find(marker) + len(marker)
        decoder = json.JSONDecoder()
        payload, _ = decoder.raw_decode(body[start:])
        return payload.get("html", "")

    # Certains endpoints peuvent renvoyer directement du HTML.
    return body


def detect_status(html):
    if not html:
        return "UNKNOWN"

    parser = TextExtractor()
    parser.feed(html)
    text = parser.get_text()

    content = f"{html} {text}".lower()

    sold_out_patterns = [
        r"outofstock",
        r"sold\s*out",
        r"sold-out",
        r"ausgebucht",
        r"épuisé",
        r"complet",
    ]

    available_patterns = [
        r"in stock",
        r"instock",
        r"available",
        r"verfügbar",
        r"disponible",
    ]

    if any(re.search(pattern, content) for pattern in sold_out_patterns):
        return "SOLD_OUT"

    if any(re.search(pattern, content) for pattern in available_patterns):
        return "AVAILABLE"

    return "UNKNOWN"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configuré : notification ignorée.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()


def check_event(name, event_id, previous_state):
    print(f"\nVérification : {name}")

    html = get_bookingkit_html(event_id)
    status = detect_status(html)

    print(f"Statut détecté : {status}")

    if status == "UNKNOWN":
        print("Statut non reconnu. Pas de notification.")
        print("Extrait de la réponse Bookingkit :")
        print(html[:1000])
        return "UNKNOWN"

    previous = previous_state.get(event_id)

    if status == "AVAILABLE" and previous != "AVAILABLE":
        message = (
            "🚨 PLACE DLR POTENTIELLEMENT DISPONIBLE !\n\n"
            f"Session : {name}\n"
            f"Statut précédent : {previous or 'Inconnu'}\n\n"
            "Vérifie immédiatement la réservation :\n"
            "https://www.european-flight-academy.com/"
            "en/registration-dlr-test"
        )

        send_telegram(message)
        print("Notification envoyée.")

    return status


def main():
    state = load_state()
    new_state = state.copy()

    for name, event_id in EVENTS.items():
        try:
            status = check_event(name, event_id, state)

            new_state[event_id] = status

        except requests.RequestException as error:
            print(f"Erreur HTTP pour {name}: {error}")

        except (ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"Erreur de parsing pour {name}: {error}")

    save_state(new_state)
    print("\nSurveillance terminée.")


if __name__ == "__main__":
    main()

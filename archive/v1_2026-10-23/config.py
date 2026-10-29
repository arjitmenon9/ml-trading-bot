"""
config.py
=========
ONE place for Fyers credentials, shared by auth.py, get_token.py,
fetch_data_fyers.py, bot.py, and fyers_execution.py.

WHY THIS EXISTS
---------------
Your original auth.py, get_token.py, and bot.py each had your App ID,
secret key, and access token hard-coded directly in the file, in plaintext.
That's a real risk the moment this project is pushed to GitHub (for a group
project, it probably will be) or shared with a professor via a repo link -
anyone with the link could see live broker credentials. It also meant every
new script that needed the token (like the new data fetcher) would need the
token copy-pasted into yet another file.

Now there's exactly one place credentials live: `fyers_secrets.json` in this
folder, which is listed in `.gitignore` so it never gets committed. You set it up
locally, once, and it's never re-typed anywhere else.

DAILY ROUTINE (Fyers access tokens expire every day - this is a Fyers
platform rule, not something we can change):
    1. python auth.py         -> prints a login URL, open it, log in,
                                  copy the "auth code" from the redirected URL
    2. python get_token.py    -> paste the auth code when prompted; this
                                  fetches a fresh access token and saves it
                                  into fyers_secrets.json automatically
    3. Now bot.py / fetch_data_fyers.py / fyers_execution.py all just work.

FIRST-TIME SETUP:
    Create fyers_secrets.json (see fyers_secrets.example.json for the shape)
    with your app_id and secret_key from https://myapi.fyers.in - the same
    values that used to be hard-coded in auth.py.
"""
import datetime
import json
from pathlib import Path

SECRETS_PATH = Path(__file__).parent / "fyers_secrets.json"
REDIRECT_URI = "http://127.0.0.1/"  # trailing slash - must match what Fyers has saved for this app


def _load() -> dict:
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(
            "fyers_secrets.json not found. Copy fyers_secrets.example.json to "
            "fyers_secrets.json and fill in your app_id + secret_key from "
            "https://myapi.fyers.in, then run auth.py and get_token.py."
        )
    return json.loads(SECRETS_PATH.read_text())


def _save(data: dict):
    SECRETS_PATH.write_text(json.dumps(data, indent=2))


def get_app_credentials():
    """Returns (app_id, secret_key) for generating login URLs / tokens."""
    data = _load()
    return data["app_id"], data["secret_key"]


def save_access_token(access_token: str):
    data = _load()
    data["access_token"] = access_token
    data["access_token_date"] = datetime.date.today().isoformat()
    _save(data)


def get_access_token() -> str:
    data = _load()
    token = data.get("access_token")
    token_date = data.get("access_token_date")
    if not token:
        raise RuntimeError("No access token saved yet. Run auth.py then get_token.py.")
    if token_date != datetime.date.today().isoformat():
        raise RuntimeError(
            f"Access token is from {token_date}, not today. Fyers tokens expire "
            f"daily - run auth.py then get_token.py again to refresh it."
        )
    return token


def get_fyers_client():
    """Returns a ready-to-use fyersModel.FyersModel, or raises a clear error."""
    from fyers_apiv3 import fyersModel
    app_id, _ = get_app_credentials()
    token = get_access_token()
    return fyersModel.FyersModel(client_id=app_id, token=token, log_path=".")

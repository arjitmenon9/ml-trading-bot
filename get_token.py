"""
Step 2 of the daily Fyers login routine. Run auth.py first, log in, then
paste what you see from the redirect page here when prompted. You can paste
EITHER just the auth_code value OR the entire redirect URL (the page that
looks broken, "can't connect to 127.0.0.1") - this script figures out which
one you gave it and pulls out the real code either way. This saves a fresh
access token into fyers_secrets.json - every other script (bot.py,
fetch_data_fyers.py, fyers_execution.py) picks it up automatically from
there, so you only ever do this in one place.
"""
import re
from urllib.parse import urlparse, parse_qs

from fyers_apiv3 import fyersModel
from config import get_app_credentials, save_access_token, REDIRECT_URI

APP_ID, SECRET_KEY = get_app_credentials()
raw = input("Paste the auth_code (or the full redirect URL) here: ").strip()


def extract_auth_code(raw_value: str) -> str:
    """Accepts either a bare auth_code or a full redirect URL and returns
    just the auth_code string Fyers expects."""
    value = raw_value.strip().strip('"').strip("'")

    # Full URL case: http://127.0.0.1/?s=ok&code=200&auth_code=XXXX&state=None
    if "auth_code=" in value:
        # Try proper URL parsing first (handles it even if pasted with extra params)
        try:
            parsed = urlparse(value)
            qs = parse_qs(parsed.query)
            if "auth_code" in qs and qs["auth_code"]:
                return qs["auth_code"][0]
        except Exception:
            pass
        # Fallback: regex out everything between auth_code= and the next & (or end of string)
        match = re.search(r"auth_code=([^&\s]+)", value)
        if match:
            return match.group(1)

    # Bare code case - just return as-is
    return value


AUTH_CODE = extract_auth_code(raw)

if AUTH_CODE != raw:
    print(f"\n(Detected a full URL - extracted just the auth_code part, {len(AUTH_CODE)} characters)")

session = fyersModel.SessionModel(
    client_id=APP_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code"
)

session.set_token(AUTH_CODE)
response = session.generate_token()

if "access_token" in response:
    save_access_token(response["access_token"])
    print("\nAccess token saved to fyers_secrets.json. Good for today.")
    print("You can now run: python bot.py  (or fetch_data_fyers.py / train_model.py)")
else:
    print("\nSomething went wrong - Fyers response:")
    print(response)
    print("\nMost likely reason: the auth_code has already expired or been used")
    print("(Fyers auth_codes are single-use and expire quickly). Run 'python auth.py'")
    print("again to get a brand new login link, log in again, and paste the NEW code.")

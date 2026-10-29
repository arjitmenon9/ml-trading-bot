"""
Step 1 of the daily Fyers login routine (tokens expire every day - see
config.py for why). Run this, open the printed URL, log in, and copy the
"auth_code" query parameter from the URL you get redirected to
(http://127.0.0.1/?s=ok&code=...&auth_code=XXXX) - you'll paste it into
get_token.py next.
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)
from fyers_apiv3 import fyersModel
from config import get_app_credentials, REDIRECT_URI

APP_ID, SECRET_KEY = get_app_credentials()

session = fyersModel.SessionModel(
    client_id=APP_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code"
)

print("Login here:", session.generate_authcode())
print("\nAfter logging in, copy the 'auth_code' value from the redirect URL")
print("and run: python src/broker/get_token.py")

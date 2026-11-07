"""Sign in with Google: checks the ID token the Google button gives the browser.

Google Identity Services gives the frontend an ID token (a JWT signed by Google). Here it is
checked with Google's public keys (google-auth library): the signature, the expiry, the issuer
(accounts.google.com), and that its audience is OUR client id, so a token made for another app
is refused. Only an email address Google has verified is accepted. No client secret is needed.
"""

from dataclasses import dataclass

from backend.config import settings


class GoogleSignInOff(Exception):
    """GOOGLE_CLIENT_ID is not set."""


class InvalidGoogleToken(Exception):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str            # Google's permanent account id; the email can change, this cannot
    email: str
    given_name: str
    family_name: str


def _verify_with_google(credential: str, client_id: str) -> dict:
    # Imported here so the app starts without the package while Google sign-in is off
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)


def verify(credential: str) -> GoogleIdentity:
    if not settings.google_client_id:
        raise GoogleSignInOff
    try:
        info = _verify_with_google(credential, settings.google_client_id)
    except ValueError as exc:  # bad signature, expired, wrong audience or issuer
        raise InvalidGoogleToken from exc
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise InvalidGoogleToken
    if info.get("email_verified") is not True or not info.get("email") or not info.get("sub"):
        raise InvalidGoogleToken
    return GoogleIdentity(
        sub=str(info["sub"]),
        email=str(info["email"]).strip().lower(),
        given_name=str(info.get("given_name") or "").strip(),
        family_name=str(info.get("family_name") or "").strip(),
    )

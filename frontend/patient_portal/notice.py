"""Privacy notice (RA 10173): the version and the text the screens show.

Version 1.1 adds contact details, home address and Sign in with Google (docs/privacy_notice_v1_1.md).
Shown on Create account, and once to a patient who accepted an older version (GET /auth/me says
consent_required). When the notice changes: update the text here, raise NOTICE_VERSION, and raise
privacy_notice_version in backend/config.py to the same value.
"""
NOTICE_VERSION = "1.1"

NEW_TITLE = "Contact details and Sign in with Google"
NEW_INTRO = "We now also ask for these details. All of them are optional."
NEW_ITEMS = [
    ("Contact number", "one mobile number."),
    ("Home address", "province, city or municipality and barangay, plus an optional street line."),
    ("Sign in with Google", "Google confirms your email address. We keep your Google account ID and your email address."),
]
# TODO (group): the purpose sentence. Replace the text in brackets when the group agrees on it.
WHY = "[To be filled in by the group]"
CLOSING = ("These details are not used by the risk models and do not appear in your reports. "
           "They are deleted when you delete your account.")

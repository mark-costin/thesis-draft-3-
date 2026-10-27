"""Sends account emails.

No mail server is set up yet (planned for deployment). Until then, in development the reset
link is printed to the API console so the flow can be tried end to end. The link is never
returned in an API response, because that would let anyone reset anyone's password.
"""

import logging

logger = logging.getLogger("cdss.mailer")

RESET_LINK = "http://localhost:3000/reset-password?token={token}"


def send_password_reset(email: str, token: str) -> None:
    link = RESET_LINK.format(token=token)
    # DEVELOPMENT ONLY: replace with a real email sender before deployment
    print(f"[dev mailer] Password reset link for {email}: {link}", flush=True)
    logger.info("Password reset email queued")

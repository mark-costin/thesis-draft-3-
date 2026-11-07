"""Forgot password: asks for the account email; the backend sends a single-use reset link."""
import reflex as rx

from ..state import State
from ..ui import hint, shell


def page() -> rx.Component:
    form = rx.form(
        rx.vstack(rx.heading("Reset your password", size="5"),
                  hint("Enter the email address of your account. We will email you a link to choose a new password."),
                  rx.input(name="email", type="email", placeholder="Email address", aria_label="Email address", required=True, size="3", custom_attrs={"autoComplete": "email"}),
                  rx.button("Send reset link", type="submit", size="3", width="100%"),
                  rx.link("Back to log in", href="/", size="2", underline="hover", text_align="center"),
                  spacing="4", align="stretch", width="100%"),
        on_submit=State.send_reset_email, width="100%")
    return shell(None, rx.center(rx.box(form, class_name="pp-card pp-auth-card", padding=rx.breakpoints(initial="22px 18px", md="28px 28px"), width="100%", max_width="420px"),
                                 width="100%", padding_top=rx.breakpoints(initial="12px", md="48px")))

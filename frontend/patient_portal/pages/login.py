"""Log in: one simple card, Facebook style. No heading, no side panel. Creating an account has its own page (/register)."""
import reflex as rx
from reflex_google_auth import google_oauth_provider

from ..auth_ui import google_button, or_divider, password_input
from ..state import GOOGLE_CLIENT_ID, State
from ..ui import shell


def login_form() -> rx.Component:
    return rx.form(
        rx.vstack(
            rx.heading("Log in", class_name="pp-sr-only", as_="h1"),  # for screen readers only
            rx.input(name="email", type="email", placeholder="Email address", aria_label="Email address", required=True, size="3", custom_attrs={"autoComplete": "username"}),
            password_input("login", placeholder="Password", aria_label="Password", required=True, size="3", custom_attrs={"autoComplete": "current-password"}),
            rx.button("Log in", type="submit", size="3", width="100%", loading=State.submitting),
            rx.link("Forgot password?", href="/forgot-password", size="2", underline="hover", text_align="center"),
            or_divider(),
            google_button("signin_with"),
            rx.divider(),
            rx.button("Create new account", type="button", variant="outline", size="3", on_click=rx.redirect("/register")),
            spacing="4", align="stretch", width="100%"),
        on_submit=State.login, width="100%")


def page() -> rx.Component:
    content = shell(None, rx.center(rx.box(login_form(), class_name="pp-card pp-auth-card", padding=rx.breakpoints(initial="22px 18px", md="28px 28px"), width="100%", max_width="420px"),
                                    width="100%", padding_top=rx.breakpoints(initial="12px", md="48px")))
    return google_oauth_provider(content, client_id=GOOGLE_CLIENT_ID) if GOOGLE_CLIENT_ID else content

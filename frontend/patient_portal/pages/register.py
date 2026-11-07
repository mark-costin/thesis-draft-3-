"""Create account: personal details, a confirmed password, home address, and consent."""
import reflex as rx
from reflex_google_auth import google_oauth_provider

from ..auth_ui import address_fields, contact_input, google_button, name_input, new_password_fields, or_divider
from ..state import GOOGLE_CLIENT_ID, State  # GOOGLE_CLIENT_ID comes from api.py (root .env)
from ..notice import NOTICE_VERSION
from ..ui import card, field, hint, privacy_notice_text, shell


def section(text: str) -> rx.Component:
    return rx.text(text, size="3", weight="bold", margin_top="6px")


def create_form() -> rx.Component:
    return rx.form(
        rx.vstack(
            section("Personal details"),
            rx.grid(
                field("First name", name_input("first_name", State.first_name, custom_attrs={"autoComplete": "given-name"})), field("Last name", name_input("last_name", State.last_name, custom_attrs={"autoComplete": "family-name"})),
                field("Date of birth", rx.input(name="dob", type="date", value=State.dob, on_change=State.set_dob, required=True, custom_attrs={"autoComplete": "bday"}),
                      tip="You must be 18 or older to use this app."),
                field("Biological sex", rx.select(["Female", "Male"], name="sex", value=State.sex, on_change=State.set_sex, placeholder="Select", required=True, width="100%"),
                      tip="Sex assigned at birth. The risk models and the family history score use it, for example for age cut-offs."),
                field("Email address", rx.input(name="email", type="email", placeholder="name@email.com", required=True, custom_attrs={"autoComplete": "email"})),
                field("Contact number", contact_input()),
                columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%"),
            section("Password"),
            rx.grid(new_password_fields(), columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%", align_items="start"),
            section("Home address"),
            rx.grid(address_fields(), columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%"),
            rx.box(privacy_notice_text(), padding="16px 18px", border="1px solid var(--accent-4)", border_radius="12px", width="100%"),
            rx.box(rx.checkbox(f"I accept the Privacy Notice (version {NOTICE_VERSION}) under RA 10173. The notice version and time of acceptance are recorded.", checked=State.consent, on_change=State.set_consent),
                   padding="16px 18px", background="var(--accent-2)", border="1px solid var(--accent-4)", border_radius="12px", width="100%"),
            rx.button("Create my account", type="submit", size="3", width="100%", loading=State.submitting, disabled=~State.consent),
            # Google sign-up shows only when GOOGLE_CLIENT_ID is set (off for now: group decision, Nov 8)
            *([or_divider(),
               rx.cond(State.consent, google_button("signup_with"),
                       rx.button(rx.icon("log-in", size=16), "Sign up with Google", type="button", variant="outline", size="3", width="100%", disabled=True)),
               hint("For Google sign-up, fill in your date of birth and biological sex and accept the Privacy Notice first. Google does not share them.")]
              if GOOGLE_CLIENT_ID else []),
            spacing="4", width="100%"),
        on_submit=State.register, reset_on_submit=False, width="100%")


def page() -> rx.Component:
    pending = rx.cond(State.google_pending,
                      rx.callout.root(rx.callout.icon(rx.icon("info")), rx.callout.text("Google signed you in, but you have no account here yet. Add your date of birth and biological sex, accept the Privacy Notice, then press Sign up with Google."),
                                      margin_bottom="16px"))
    content = shell(None, rx.center(card(rx.heading("Create account", size="6"), hint("Fill in your details to get started.", margin_bottom="18px", margin_top="4px"), pending, create_form(),
                                         rx.center(rx.text("Already have an account? ", rx.link("Log in", href="/", weight="medium"), size="2"), margin_top="20px"),
                                         max_width="720px", padding=rx.breakpoints(initial="24px 18px", md="32px 36px"), class_name="pp-card pp-auth-card"), width="100%"))
    return google_oauth_provider(content, client_id=GOOGLE_CLIENT_ID) if GOOGLE_CLIENT_ID else content

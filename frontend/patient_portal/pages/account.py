"""Account settings: every personal detail in one place, plus security & authentication."""
import reflex as rx

from ..auth_ui import PwState, address_fields, contact_input, match_check, match_message, name_input, password_input, strength_meter
from ..state import State
from ..ui import card, field, hint, page_header, shell, title


def personal_details() -> rx.Component:
    return card(
        title("Personal details", icon="user"),
        rx.form(
            rx.grid(
                field("First name", name_input("first_name", State.first_name)),
                field("Last name", name_input("last_name", State.last_name)),
                field("Date of birth", rx.input(name="dob", type="date", default_value=State.dob, required=True)),
                field("Biological sex", rx.select(["Female", "Male"], name="sex", default_value=State.sex, placeholder="Select", width="100%"),
                      tip="Sex assigned at birth. The risk models use it."),
                field("Email address", rx.input(name="email", type="email", default_value=State.email, required=True, read_only=State.auth_provider == "google"),
                      tip="A Google account keeps the email address that Google verified."),
                field("Contact number", contact_input(default_value=State.contact, required=False)),
                columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%"),
            rx.text("Home address", size="3", weight="bold", margin_top="24px", margin_bottom="12px", padding_top="20px", border_top="1px solid var(--pp-border)"),
            rx.grid(address_fields(), columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%"),
            rx.button(rx.icon("save", size=16), "Save changes", type="submit", size="3", margin_top="24px"),
            on_submit=State.save_profile, reset_on_submit=False))


def password_form() -> rx.Component:
    check = rx.call_script(match_check("new_password", "confirm_password"))
    return rx.form(
        rx.vstack(
            field("Current password", password_input("cur", "current_password", id="current_password", required=True, custom_attrs={"autoComplete": "current-password"})),
            field("New password", rx.vstack(password_input("new", "new_password", id="new_password", min_length=12, placeholder="At least 12 characters", required=True, custom_attrs={"autoComplete": "new-password"}, on_change=[PwState.check_strength, check]),
                                           strength_meter(), spacing="2", align="stretch", width="100%")),
            field("Confirm password", rx.vstack(password_input("conf", "confirm_password", id="confirm_password", required=True, custom_attrs={"autoComplete": "new-password"}, on_change=check),
                                                match_message("confirm_password"), spacing="2", align="stretch", width="100%")),
            rx.hstack(rx.button(rx.icon("key-round", size=16), "Change password", type="submit", size="3"),
                      rx.button("Forgot password?", type="button", variant="ghost", on_click=State.send_reset_email), spacing="4", align="center", wrap="wrap"),
            hint("Forgot your current password? We email a single-use reset link to ", rx.text.strong(rx.cond(State.email != "", State.email, "your account email")), ". It works for one hour."),
            spacing="4", align="stretch", width="100%", max_width="480px"),
        on_submit=State.change_password, reset_on_submit=False)


def security() -> rx.Component:
    return card(title("Security & authentication", icon="shield-check"),
                rx.cond(State.auth_provider == "google",
                        rx.hstack(rx.icon("badge-check", size=18, color="var(--accent-9)"),
                                  rx.text("You sign in with Google, so there is no password to change here. Manage it in your Google account.", size="2"), spacing="2", align="start"),
                        password_form()))


def page() -> rx.Component:
    return shell("Account settings",
                 page_header("Account settings", "Your personal details and how you sign in.", eyebrow="Your account"),
                 personal_details(), rx.box(height="24px"), security())

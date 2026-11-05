"""Choose a new password from the emailed link (/reset-password?token=...)."""
import reflex as rx

from ..auth_ui import new_password_fields
from ..state import State
from ..ui import hint, shell


def page() -> rx.Component:
    form = rx.form(
        rx.vstack(rx.heading("Choose a new password", size="5"),
                  hint("The link works once and for one hour. After the change, every device is logged out."),
                  new_password_fields("reset_password", "reset_confirm"),
                  rx.button("Save new password", type="submit", size="3", width="100%"),
                  rx.link("Back to log in", href="/", size="2", underline="hover", text_align="center"),
                  spacing="4", align="stretch", width="100%"),
        on_submit=State.confirm_reset, width="100%")
    return shell(None, rx.center(rx.box(form, class_name="pp-card pp-auth-card", padding=rx.breakpoints(initial="22px 18px", md="28px 28px"), width="100%", max_width="460px"),
                                 width="100%", padding_top=rx.breakpoints(initial="12px", md="48px")))

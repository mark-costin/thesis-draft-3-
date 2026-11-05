"""Pieces shared by the public pages (log in, create account, forgot password) and Account settings."""
import reflex as rx
from reflex_google_auth.google_auth import GoogleLogin

from .state import GOOGLE_CLIENT_ID, State
from .ui import field


class GoogleButton(GoogleLogin):
    """Google's own sign-in button (the library component, with the styling props added)."""
    theme: rx.Var[str]
    size: rx.Var[str]
    text: rx.Var[str]
    shape: rx.Var[str]
    width: rx.Var[str]


class PwState(rx.State):
    """Show/hide toggles and the strength meter. UI only: only the length and score are kept, never the password."""
    shown: dict[str, bool] = {}
    pw_len: int = 0
    pw_score: int = 0  # 0 to 4

    @rx.event
    def toggle_show(self, key: str):
        self.shown = {**self.shown, key: not self.shown.get(key, False)}

    @rx.event
    def check_strength(self, value: str):
        self.pw_len = len(value)
        self.pw_score = sum([len(value) >= 12, any(c.islower() for c in value) and any(c.isupper() for c in value), any(c.isdigit() for c in value), any(not c.isalnum() for c in value)])

    @rx.var
    def pw_label(self) -> str:
        if self.pw_len == 0:
            return ""
        if self.pw_len < 12:
            return f"{self.pw_len} of 12 characters"
        return ["Weak", "Weak", "Fair", "Good", "Strong"][self.pw_score]

    @rx.var
    def pw_color(self) -> str:
        return "gray" if self.pw_len == 0 else ("red" if self.pw_len < 12 or self.pw_score <= 1 else ("amber" if self.pw_score == 2 else "green"))

    @rx.var
    def pw_pct(self) -> int:
        return 0 if self.pw_len == 0 else min(100, max(10, self.pw_score * 25)) if self.pw_len >= 12 else min(40, round(40 * self.pw_len / 12))


def password_input(key: str, name: str = "password", **props) -> rx.Component:
    """Password box with an eye button. ``key`` tells the show/hide toggles apart; ``name`` is the form field name."""
    shown = PwState.shown[key]
    return rx.input(
        rx.input.slot(
            rx.icon_button(rx.cond(shown, rx.icon("eye-off", size=16), rx.icon("eye", size=16)), type="button", variant="ghost", color_scheme="gray", size="1",
                           on_click=PwState.toggle_show(key), aria_label=rx.cond(shown, "Hide password", "Show password")),
            side="right"),
        name=name, type=rx.cond(shown, "text", "password"), **props)


def strength_meter() -> rx.Component:
    """Compact: the bar with its one-word verdict beside it."""
    return rx.hstack(rx.progress(value=PwState.pw_pct, color_scheme=PwState.pw_color, size="1", aria_label="Password strength", flex="1"),
                     rx.text(PwState.pw_label, size="1", color="var(--pp-ink-soft)", min_width="92px", text_align="right", aria_live="polite"), spacing="3", align="center", width="100%")


def match_check(first_id: str, second_id: str) -> str:
    """Browser-side script: compares the two password boxes and writes the result into the message under them.
    It runs in the browser only, so the passwords are never sent to the server just to be compared."""
    return ("(function(){var a=document.getElementById('%s'),b=document.getElementById('%s'),m=document.getElementById('%s_msg');"
            "if(!a||!b||!m)return;if(!b.value){m.textContent='';m.removeAttribute('data-state');return;}"
            "var ok=a.value===b.value;m.textContent=ok?'\u2713 Passwords match':'\u2715 Passwords do not match';m.setAttribute('data-state',ok?'ok':'no');})()") % (first_id, second_id, second_id)


def match_message(second_id: str) -> rx.Component:
    return rx.el.div(id=f"{second_id}_msg", class_name="pp-match", role="status", aria_live="polite")


def new_password_fields(first_id: str = "reg_password", second_id: str = "reg_confirm") -> rx.Component:
    """Password (with strength bar) + Confirm password (with a live 'match / do not match' message)."""
    check = rx.call_script(match_check(first_id, second_id))
    return rx.fragment(
        field("Password", rx.vstack(password_input("pw", "password", id=first_id, placeholder="At least 12 characters", min_length=12, required=True, custom_attrs={"autoComplete": "new-password"}, on_change=[PwState.check_strength, check]),
                                    strength_meter(), spacing="2", align="stretch", width="100%")),
        field("Confirm password", rx.vstack(password_input("pw2", "confirm_password", id=second_id, required=True, custom_attrs={"autoComplete": "new-password"}, on_change=check),
                                            match_message(second_id), spacing="2", align="stretch", width="100%")))


NAME_FILTER = "var e=document.getElementById('%s');if(e){e.value=e.value.replace(/[^\\p{L}\\s.'\u2019-]/gu,'');}"
PHONE_FILTER = "var e=document.getElementById('%s');if(e){var v=e.value.replace(/[^0-9+]/g,'');e.value=v.charAt(0)==='+'?'+'+v.slice(1).replace(/\\+/g,''):v.replace(/\\+/g,'');}"


def name_input(name: str, default_value=None, **props) -> rx.Component:
    """Letters only: digits and symbols are removed as the person types (also checked on the server)."""
    kw = {"default_value": default_value} if default_value is not None else {}
    return rx.input(name=name, id=f"{name}_input", required=True, pattern=r"[\p{L} .'\u2019\-]+", title="Letters only. No numbers.", max_length=60,
                    on_change=rx.call_script(NAME_FILTER % f"{name}_input"), **kw, **props)


def or_divider() -> rx.Component:
    return rx.hstack(rx.divider(), rx.text("or", size="2", color="var(--pp-ink-soft)"), rx.divider(), align="center", spacing="3", width="100%")


def google_button(label: str, on_success=None) -> rx.Component:
    """Real button when GOOGLE_CLIENT_ID is set; otherwise a disabled one that says how to turn it on."""
    if not GOOGLE_CLIENT_ID:
        return rx.vstack(rx.button(rx.icon("log-in", size=16), "Continue with Google", type="button", variant="outline", size="3", width="100%", disabled=True),
                         rx.text("Google sign-in is off. Set GOOGLE_CLIENT_ID to turn it on (see README).", size="1", color="var(--pp-ink-soft)"), spacing="1", width="100%")
    return rx.center(GoogleButton.create(on_success=on_success or State.google_signed_in, text=label, shape="rectangular", theme="outline", size="large", width="340"), width="100%")


def contact_input(**props) -> rx.Component:
    """Numbers only (and a leading +): letters are removed as the person types (also checked on the server)."""
    return rx.input(name="contact", id="contact_input", type="tel", placeholder="09171234567", pattern=r"(09|\+639)[0-9]{9}", title="A mobile number like 09171234567 or +639171234567",
                    max_length=13, custom_attrs={"autoComplete": "tel"}, required=props.pop("required", True), on_change=rx.call_script(PHONE_FILTER % "contact_input"), **props)


def address_fields() -> rx.Component:
    """Home address: Province, City or municipality and Barangay are drop-downs (A to Z) that depend on each other."""
    return rx.fragment(
        field("Province", rx.select(State.provinces, value=State.province, on_change=State.set_province, placeholder="Select province", width="100%", required=True),
              tip="Used only to contact you and to show health services near you. It is not used by the risk models."),
        field("City or municipality", rx.select(State.city_options, value=State.city, on_change=State.set_city, placeholder="Select city or municipality", disabled=State.province == "", width="100%", required=True)),
        field("Barangay", rx.select(State.barangay_options, value=State.barangay, on_change=State.set_barangay, placeholder="Select barangay", disabled=State.city == "", width="100%", required=True)),
        field("House no., street, subdivision (optional)", rx.input(name="street", default_value=State.street, placeholder="e.g. 12 Mabini St., Phase 2", custom_attrs={"autoComplete": "street-address"})),
        rx.cond(State.lists_incomplete, rx.text("The full city and barangay lists could not be downloaded, so only a starter list is shown. Connect to the internet and restart the app once.", size="1", color="var(--pp-ink-soft)", grid_column="1 / -1")))

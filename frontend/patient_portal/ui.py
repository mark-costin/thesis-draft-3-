"""Shared building blocks: card, field, top bar, page shell.

Visual tokens live in assets/theme.css (class names start with ``pp-``).
One brand colour (clinic teal-blue); green / amber / red appear only to show status.
Public helpers keep their original signatures, so every page keeps working.
"""
import reflex as rx

from .insights import Insights
from .state import State

NAV = [("Dashboard", "/dashboard"), ("Vitals & lifestyle", "/vitals"), ("Questionnaires", "/questionnaires"), ("Family history", "/family"), ("Scan & assess", "/scan"), ("Reports", "/reports")]
NAV_ICONS = {"Dashboard": "layout-dashboard", "Vitals & lifestyle": "activity", "Questionnaires": "clipboard-list", "Family history": "users", "Scan & assess": "scan-line", "Reports": "file-text"}

# Kept for pages that import it; the look itself now comes from the .pp-card class.
CARD = dict(width="100%", padding=rx.breakpoints(initial="20px", md="26px 28px"))


def card(*children, **props) -> rx.Component:
    """Surface container. Pass ``lift=True`` for a hover-raise effect on clickable cards."""
    lift = props.pop("lift", False)
    cls = "pp-card pp-card-lift" if lift else "pp-card"
    return rx.box(*children, **{**CARD, "class_name": cls, **props})


def title(*text, icon: str | None = None, **props) -> rx.Component:
    """Card heading, optionally with a leading icon."""
    head = rx.heading(*text, size="5", weight="bold", **props)
    if icon is None:
        return rx.box(head, margin_bottom="16px")
    return rx.hstack(rx.center(rx.icon(icon, size=18), width="34px", height="34px", border_radius="50%", class_name="pp-icon-tile", flex_shrink="0"),
                     head, spacing="3", align="center", margin_bottom="16px")


def page_header(heading, subtitle=None, eyebrow: str | None = None, right: rx.Component | None = None) -> rx.Component:
    """Top-of-page heading block: eyebrow, H1, one-line description, optional right-side slot."""
    left = rx.vstack(
        rx.text(eyebrow, class_name="pp-eyebrow") if eyebrow else rx.fragment(),
        rx.heading(*(heading if isinstance(heading, tuple) else (heading,)), size=rx.breakpoints(initial="6", md="7"), as_="h1", weight="bold"),
        rx.text(subtitle, size="3", color="var(--pp-ink-soft)", max_width="680px") if subtitle is not None else rx.fragment(),
        spacing="1", align="start")
    return rx.flex(left, right if right is not None else rx.fragment(), justify="between", align=rx.breakpoints(initial="start", md="end"),
                   direction=rx.breakpoints(initial="column", md="row"), gap="14px", width="100%", margin_bottom=rx.breakpoints(initial="20px", md="26px"))


def hint(*text, **props) -> rx.Component:
    """Small muted helper / disclaimer text."""
    return rx.text(*text, size="2", color="var(--pp-ink-soft)", **props)


def field(label: str, control: rx.Component, tip: str | None = None) -> rx.Component:
    """Label + control. ``tip`` adds an (i) icon with a plain-language explanation (hover, focus or tap)."""
    lab = rx.text(label, size="2", weight="bold", color="var(--pp-ink)")
    if tip:
        lab = rx.hstack(lab, rx.tooltip(rx.box(rx.icon("info", size=14), tab_index=0, role="img", aria_label=tip, cursor="help", color="var(--pp-ink-soft)", display="flex"), content=tip),
                        spacing="1", align="center")
    return rx.vstack(lab, control, spacing="1", align="stretch", width="100%")


def empty_state(icon: str, heading: str, text: str, action: rx.Component | None = None) -> rx.Component:
    """Friendly placeholder when a list has nothing in it yet."""
    return rx.vstack(rx.center(rx.icon(icon, size=26), width="56px", height="56px", border_radius="50%", class_name="pp-icon-tile"),
                     rx.text(heading, weight="bold", size="4", text_align="center"), rx.text(text, size="2", color="var(--pp-ink-soft)", text_align="center", max_width="420px"),
                     action if action is not None else rx.fragment(), class_name="pp-empty", align="center", spacing="3", padding="32px 20px", width="100%")


STEPS = ["Upload", "Verify", "Assess", "Results"]


def stepper(current: int) -> rx.Component:
    """Progress through scan -> results. ``current`` is 1-based; earlier steps show a check mark."""
    items = []
    for i, name in enumerate(STEPS, 1):
        done, on = i < current, i == current
        dot = rx.center(rx.icon("check", size=15, stroke_width=3) if done else rx.text(str(i), size="2", weight="bold"), width="30px", height="30px", border_radius="50%", flex_shrink="0",
                        class_name="pp-step-dot", background="var(--pp-brand)" if (done or on) else "var(--pp-surface-muted)", color="white" if (done or on) else "var(--pp-ink-soft)",
                        border="2px solid var(--pp-brand)" if (done or on) else "2px solid var(--pp-border-strong)")
        label = rx.text(name, size="2", weight="bold" if on else "medium", color="var(--pp-ink)" if on else "var(--pp-ink-soft)", display=rx.breakpoints(initial="block" if on else "none", sm="block"))
        items.append(rx.hstack(dot, label, spacing="2", align="center", **({"aria_current": "step"} if on else {})))
        if i < len(STEPS):
            items.append(rx.box(height="2px", flex="1", min_width="14px", border_radius="2px", background="var(--pp-brand)" if done else "var(--pp-border)"))
    return rx.hstack(*items, align="center", spacing="3", width="100%", margin_bottom=rx.breakpoints(initial="18px", md="24px"), aria_label="Assessment progress", role="group")


def cols(n: int):
    """Radix Grid columns: 1 on phones, 2 from tablet portrait (sm, 768px), n from laptops (lg, 1280px)."""
    return rx.breakpoints(initial="1", sm="2", lg=str(n)) if n > 2 else rx.breakpoints(initial="1", sm=str(n))


def brand() -> rx.Component:
    """Logo mark only. It links home."""
    return rx.link(rx.center(rx.icon("heart-pulse", size=22, color="white", stroke_width=2.5), width="42px", height="42px", border_radius="12px", background="var(--accent-9)", flex_shrink="0"),
                   href="/", underline="none", aria_label="Patient Portal home")


def nav_link(label: str, href: str, active: str) -> rx.Component:
    on = label == active
    extra = {"aria_current": "page"} if on else {}
    return rx.link(rx.icon(NAV_ICONS.get(label, "circle"), size=17), label, href=href, underline="none", class_name="pp-nav-link", **extra)


def _first_working(*makers):
    """Returns the first thing that builds without an error. Reflex has moved its colour-mode helpers between versions, so try each known place."""
    for make in makers:
        try:
            return make()
        except (AttributeError, TypeError):
            continue
    return None


def set_mode(mode: str):
    """Event that switches the theme to "light", "dark" or "system"."""
    return _first_working(lambda: rx.color_mode.set_color_mode(mode), lambda: rx.set_color_mode(mode), lambda: rx.color_mode.set(mode))


def toggle_mode():
    """Event that flips between light and dark."""
    return _first_working(lambda: rx.toggle_color_mode, lambda: rx.color_mode.toggle_color_mode)


def theme_toggle() -> rx.Component:
    return rx.icon_button(rx.color_mode_cond(light=rx.icon("moon", size=18), dark=rx.icon("sun", size=18)), on_click=toggle_mode(),
                          variant="soft", color_scheme="gray", size="2", aria_label="Switch light or dark mode")


def bell() -> rx.Component:
    """Reminders: lists whatever the checklist still misses; badge shows the count."""
    row = lambda m: rx.hstack(rx.icon("circle-alert", size=16, color="var(--amber-10)"), rx.text(m, size="2"), spacing="2", align="center")
    return rx.popover.root(
        rx.popover.trigger(rx.box(rx.icon_button(rx.icon("bell", size=18), variant="soft", color_scheme="gray", size="2", aria_label="Reminders"),
                                  rx.cond(Insights.missing_count > 0, rx.badge(Insights.missing_count, variant="solid", radius="full", size="1", position="absolute", top="-6px", right="-6px")),
                                  position="relative")),
        rx.popover.content(
            rx.text("Reminders", weight="bold", size="3", margin_bottom="8px"),
            rx.cond(Insights.missing_count > 0,
                    rx.vstack(rx.text("Still needed before your next assessment:", size="2", color="var(--pp-ink-soft)"), rx.foreach(Insights.missing_items, row),
                              rx.link("Go to Scan & assess", href="/scan", weight="bold", size="2"), spacing="2", align="start"),
                    rx.hstack(rx.icon("circle-check", size=16, color="var(--green-10)"), rx.text("You are all caught up.", size="2"), spacing="2", align="center")),
            max_width="300px"))


def user_menu() -> rx.Component:
    """Name and avatar open a drop-down: Profile & account, Theme (Light / Dark / System) and Log out."""
    chip = rx.button(
        rx.avatar(src=State.picture, fallback=State.initials, size="3", radius="full", variant="soft"),
        rx.vstack(rx.text(State.full_name, weight="bold", size="3", white_space="nowrap", line_height="1.2"),
                  rx.text(State.email, size="1", color="var(--pp-ink-soft)"),
                  spacing="0", align="start", display=rx.breakpoints(initial="none", xl="flex")),
        rx.icon("chevron-down", size=16, color="var(--pp-ink-soft)"),
        variant="ghost", color_scheme="gray", class_name="pp-usermenu", aria_label="Account menu")
    return rx.menu.root(
        rx.menu.trigger(chip),
        rx.menu.content(
            rx.box(rx.text(State.full_name, weight="bold", size="3"), rx.text(State.email, size="1", color="var(--pp-ink-soft)"), padding="6px 10px 8px"),
            rx.menu.separator(),
            rx.menu.item(rx.hstack(rx.icon("user-round", size=16), "Profile & account", spacing="2", align="center"), on_click=rx.redirect("/account")),
            rx.menu.sub(
                rx.menu.sub_trigger(rx.hstack(rx.icon("palette", size=16), "Theme", spacing="2", align="center")),
                rx.menu.sub_content(
                    rx.menu.item(rx.hstack(rx.icon("sun", size=16), "Light", spacing="2", align="center"), on_click=set_mode("light")),
                    rx.menu.item(rx.hstack(rx.icon("moon", size=16), "Dark", spacing="2", align="center"), on_click=set_mode("dark")),
                    rx.menu.item(rx.hstack(rx.icon("monitor", size=16), "Match my device", spacing="2", align="center"), on_click=set_mode("system")))),
            rx.menu.separator(),
            rx.menu.item(rx.hstack(rx.icon("log-out", size=16), "Log out", spacing="2", align="center"), on_click=State.logout),
            min_width="220px"))


def topbar(active: str | None) -> rx.Component:
    if active is None:
        mid, right = rx.fragment(), rx.hstack(theme_toggle(), rx.fragment(), spacing="3", align="center")
    else:
        mid = rx.fragment(
            rx.el.nav(rx.hstack(*[nav_link(l, h, active) for l, h in NAV], spacing="1"), aria_label="Main navigation", display=rx.breakpoints(initial="none", lg="block")),
            rx.menu.root(rx.menu.trigger(rx.icon_button(rx.icon("menu"), variant="soft", size="3", aria_label="Open navigation menu", display=rx.breakpoints(initial="flex", lg="none"))),
                         rx.menu.content(*[rx.menu.item(rx.link(rx.hstack(rx.icon(NAV_ICONS.get(l, "circle"), size=16), l, spacing="2", align="center"), href=h, underline="none")) for l, h in NAV])))
        right = rx.hstack(bell(), user_menu(), spacing="2", align="center")
    return rx.hstack(brand(), mid, right, justify="between", align="center", spacing="5", width="100%", class_name="pp-topbar",
                     padding=rx.breakpoints(initial="10px 16px", md="12px 32px", lg="12px 40px", xl="12px 64px"))


def footer() -> rx.Component:
    return rx.center(rx.text("Educational screening tool, not a medical diagnosis. Your records stay on your own server.", size="1", color="var(--pp-ink-soft)", text_align="center"),
                     padding="28px 20px 36px", width="100%")


def shell(active: str | None, *children) -> rx.Component:
    page_body = rx.box(
        rx.link("Skip to main content", href="#main", class_name="pp-skip"),
        topbar(active),
        rx.el.main(*children, id="main", max_width="1312px", margin="0 auto", padding=rx.breakpoints(initial="20px 16px", md="30px 40px 20px", lg="34px 64px 20px")),
        footer(), min_height="100vh")
    if active is None:  # sign-in screen is public
        return page_body
    # Signed-in pages show nothing private until the login check passes (no flash of someone's data before the redirect).
    return rx.cond(State.is_logged_in, page_body,
                   rx.center(rx.vstack(rx.spinner(size="3"), rx.text("Taking you to sign in...", size="2", color="var(--pp-ink-soft)"), align="center", spacing="3"), min_height="100vh"))

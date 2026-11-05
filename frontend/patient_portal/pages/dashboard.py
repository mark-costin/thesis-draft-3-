"""Screen 2: Health Dashboard (Card, Badge, Line Chart, Callout, Button)."""
import reflex as rx

from ..insights import Insights
from ..state import Check, Risk, State
from ..ui import card, cols, hint, page_header, shell, title


def icon_tile(code) -> rx.Component:
    icon = rx.match(code, ("CVD", rx.icon("heart-pulse", size=20)), ("T2D", rx.icon("droplet", size=20)), ("CRD", rx.icon("wind", size=20)),
                    ("NEURO", rx.icon("brain", size=20)), rx.icon("activity", size=20))
    return rx.center(icon, width="40px", height="40px", border_radius="50%", class_name="pp-icon-tile", flex_shrink="0")


def risk_card(r: Risk) -> rx.Component:
    # Tier shows through the badge and the bar (green / amber / red), always with the tier name in text.
    return card(rx.hstack(rx.hstack(icon_tile(r.code), rx.text(r.name, weight="bold", size="3", line_height="1.25"), spacing="3", align="center"),
                          rx.badge(r.tier, color_scheme=r.color, variant="soft", radius="full", size="2"), justify="between", align="start", width="100%", spacing="2"),
                rx.progress(value=r.score, color_scheme=r.color, margin_y="20px", aria_label="Risk level"),
                rx.text(r.note, size="2", color="var(--pp-ink-soft)"),
                padding="20px 22px", lift=True)


def stat(label: str, value, icon: str, key: str) -> rx.Component:
    """Value + 'in range' badge + change since the previous entry (from Insights.vital_flags)."""
    f = Insights.vital_flags[key]
    arrow = rx.match(f["trend"], ("up", rx.icon("trending-up", size=14)), ("down", rx.icon("trending-down", size=14)), rx.icon("minus", size=14))
    return rx.hstack(
        rx.center(rx.icon(icon, size=18), width="36px", height="36px", border_radius="50%", class_name="pp-icon-tile", flex_shrink="0"),
        rx.vstack(rx.text(label, size="2", weight="medium", color="var(--pp-ink-soft)"),
                  rx.text(value, size="6", weight="bold", line_height="1.1", class_name="pp-num"),
                  rx.hstack(rx.badge(f["label"], color_scheme=f["color"], variant="soft", size="1"),
                            rx.hstack(arrow, rx.text(f["delta"], size="1", class_name="pp-num"), spacing="1", align="center", color="var(--pp-ink-soft)", aria_label="Change since last entry"),
                            spacing="2", align="center", wrap="wrap"),
                  spacing="1", align="start"),
        class_name="pp-panel", padding="14px 16px", spacing="3", align="center", flex="1", min_width="200px")


def chart_box(name: str, chart: rx.Component) -> rx.Component:
    return rx.box(rx.text(name, weight="bold", size="3", margin_bottom="8px"), chart, class_name="pp-panel", padding="16px 18px", flex="1", min_width="260px")


def line(key: str, color: str) -> rx.Component:
    return rx.recharts.line(data_key=key, stroke=color, stroke_width=2.5, dot=True)


def bp_chart() -> rx.Component:
    return rx.recharts.line_chart(
        rx.recharts.cartesian_grid(stroke_dasharray="3 4", vertical=False), rx.recharts.x_axis(data_key="day", font_size=11, tick_line=False),
        rx.recharts.y_axis(domain=["auto", "auto"], font_size=11, tick_line=False, axis_line=False),
        rx.recharts.reference_line(y=120, stroke="#8AA0A6", stroke_dasharray="6 5"), rx.recharts.reference_line(y=80, stroke="#8AA0A6", stroke_dasharray="6 5"),
        line("systolic", rx.color_mode_cond(light="#12262B", dark="#EAF3F5")), line("diastolic", rx.color_mode_cond(light="#0B7285", dark="#5CC4D6")), rx.recharts.graphing_tooltip(), data=State.bp_trend, width="100%", height=200)


def bmi_chart() -> rx.Component:
    return rx.recharts.line_chart(rx.recharts.cartesian_grid(stroke_dasharray="3 4", vertical=False), rx.recharts.x_axis(data_key="day", font_size=11, tick_line=False),
                                  rx.recharts.y_axis(domain=["auto", "auto"], font_size=11, tick_line=False, axis_line=False),
                                  line("bmi", rx.color_mode_cond(light="#0B7285", dark="#5CC4D6")), rx.recharts.graphing_tooltip(), data=State.bmi_trend, width="100%", height=200)


def check_row(c: Check) -> rx.Component:
    """One input the models still need (from GET /assessment/readiness), with a link to where it is entered."""
    return rx.hstack(rx.checkbox(checked=c.done, disabled=True, aria_label=c.label),
                     rx.vstack(rx.text(c.label), rx.link(c.where, href=c.href, size="1"), spacing="0", align="start", flex="1"),
                     rx.badge(c.tag, variant="soft", color_scheme=rx.cond(c.done, "green", "gray")),
                     align="center", padding="12px 8px", border_top="1px solid var(--pp-border)", width="100%", class_name="pp-row")


def no_results() -> rx.Component:
    return card(rx.hstack(rx.icon("info", size=20), rx.text("No assessment yet. Fill in what is missing below, then start your first assessment.", size="3"),
                          spacing="3", align="center"), padding="20px 22px")


def page() -> rx.Component:
    latest = State.latest_vitals
    return shell(
        "Dashboard",
        page_header(("Welcome back, ", State.first_name), "Here is where you stand: your latest screening results, and what we still need for your next assessment.",
                    eyebrow="Your health dashboard"),
        title("Latest risk results", icon="shield-check"),
        rx.cond(State.has_results, rx.grid(rx.foreach(State.risks, risk_card), columns=cols(4), spacing="4", width="100%"), no_results()),
        hint("Educational screening only. Not a medical diagnosis.", margin_top="12px", margin_bottom="30px"),
        rx.grid(
            card(title("Recent vitals and trends", icon="activity"),
                 rx.flex(stat("Blood pressure", latest.bp, "heart-pulse", "bp"), stat("Heart rate", latest.hr + " bpm", "activity", "hr"), stat("BMI", latest.bmi, "scale", "bmi"), stat("SpO2", latest.spo2 + "%", "wind", "spo2"),
                         gap="3", wrap="wrap", margin_bottom="16px"),
                 rx.cond(State.has_vitals, rx.flex(chart_box("Blood pressure (mmHg)", bp_chart()), chart_box("BMI", bmi_chart()), gap="3", wrap="wrap"),
                         hint("No vitals yet. ", rx.link("Log your first reading", href="/vitals", weight="bold"), ".")),
                 hint("In-range labels use general reference ranges. They are a guide, not a diagnosis.", margin_top="14px")),
            card(title("Missing information", icon="clipboard-list"),
                 rx.cond(State.checklist.length() > 0, rx.foreach(State.checklist, check_row),
                         rx.hstack(rx.icon("circle-check", size=18, color="var(--green-10)"), rx.text("Nothing is missing. You can run an assessment.", size="2"), spacing="2", align="center")),
                 hint("An assessment starts only when nothing required is missing.", margin_top="16px", margin_bottom="16px"),
                 rx.button("Start assessment", rx.icon("arrow-right", size=18), size="3", width="100%", on_click=rx.redirect("/scan"))),
            grid_template_columns=rx.breakpoints(initial="1fr", lg="1.6fr 1fr"), gap="22px", align_items="start", width="100%"))

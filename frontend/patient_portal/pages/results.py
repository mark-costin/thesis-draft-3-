"""Screen 6: Risk Results and Care Plan (Tabs-style selector, Progress and Card, Bar Chart, Callout, Button)."""
import reflex as rx

from ..state import Risk, State
from ..ui import card, cols, empty_state, hint, page_header, shell, stepper, title
from .dashboard import icon_tile


def tier_card(r: Risk) -> rx.Component:
    on = State.selected == r.code
    # A real <button>: reachable with Tab, picked with Enter or Space, announced as pressed / not pressed.
    return rx.el.button(
        rx.box(rx.hstack(rx.hstack(icon_tile(r.code), rx.text(r.short, weight="bold"), spacing="2", align="center"), rx.badge(r.tier, color_scheme=r.color, variant="soft"), justify="between", width="100%"),
               rx.progress(value=r.score, color_scheme=r.color, margin_top="14px"), padding="18px 20px", border_radius="18px", width="100%",
               class_name="pp-card pp-card-lift", background=rx.cond(on, "var(--accent-2)", "var(--pp-surface)"),
               border=rx.cond(on, "2px solid var(--accent-9)", "1px solid var(--pp-border)"),
               box_shadow=rx.cond(on, "0 0 0 4px var(--accent-4)", "var(--pp-shadow-sm)")),
        type="button", on_click=State.select_disease(r.code), class_name="pp-choice", aria_pressed=on, aria_label=r.short + " risk: " + r.tier)


def shap_chart() -> rx.Component:
    return rx.recharts.bar_chart(
        rx.recharts.bar(data_key="raises", fill="#B7791F", stack_id="s", radius=4), rx.recharts.bar(data_key="lowers", fill="#2F7D5B", stack_id="s", radius=4),
        rx.recharts.x_axis(type_="number", domain=["auto", "auto"], hide=True), rx.recharts.y_axis(data_key="name", type_="category", width=132, font_size=12),
        rx.recharts.reference_line(x=0, stroke="#8AA0A6"), rx.recharts.graphing_tooltip(), data=State.reasons, layout="vertical", width="100%", height=210)


def callout(color: str, icon: str, text: str, **kw) -> rx.Component:
    return rx.callout.root(rx.callout.icon(rx.icon(icon)), rx.callout.text(text), color_scheme=color, **kw)


def tier_goal() -> rx.Component:
    return rx.match(State.current.tier, ("Low", "Routine yearly prevention. Keep up these habits."), ("Moderate", "Lifestyle changes, with a follow-up in 3 to 6 months."),
                    "A doctor visit for a check within 30 to 60 days, plus the habits below.")


def plan_row(i) -> rx.Component:
    goal_btn = rx.cond(i["in_goals"], rx.badge("In your goals", color_scheme="green", variant="soft"),
                       rx.button("Add as goal", size="1", variant="soft", on_click=State.add_goal(i["text"], i["category"])))
    return rx.hstack(rx.vstack(rx.text(i["text"], weight="medium"), rx.text(i["why"], size="2", color="var(--pp-ink-soft)"),
                               rx.hstack(rx.badge(i["label"], variant="soft", color_scheme="gray", size="1"), rx.cond(i["addon"], rx.badge("Based on your answers", variant="soft", color_scheme="teal", size="1")),
                                         rx.badge(i["basis"], variant="outline", color_scheme=i["basis_color"], size="1"), rx.text(i["source"], size="1", color="var(--pp-ink-soft)"), spacing="2", align="center", wrap="wrap"),
                               spacing="1", align="start", flex="1"),
                     rx.cond(i["goal"], goal_btn), align="start", spacing="4", padding="14px 8px", border_top="1px solid var(--pp-border)", width="100%", class_name="pp-row")


def alert_row(i) -> rx.Component:
    """Urgent warning signs and the crisis hotline get their own red box, so they stay visible."""
    return rx.callout.root(rx.callout.icon(rx.icon("siren")), rx.callout.text(rx.text.strong(i["label"], ": "), i["text"]), color_scheme="red", variant="soft", margin_top="12px", width="100%")


def care_plan_card() -> rx.Component:
    return card(rx.hstack(rx.heading(State.plan_title, size="4"), rx.badge(State.plan["id"], variant="soft", color_scheme="gray"),
                          rx.cond(State.plan_pending, rx.badge("Pending physician review", color_scheme="amber", variant="soft")), spacing="2", align="center", wrap="wrap", margin_bottom="6px"),
                rx.text(tier_goal(), size="3", color="var(--pp-ink-soft)", margin_bottom="8px"),
                rx.foreach(State.plan_actions, plan_row),
                rx.foreach(State.plan_alerts, alert_row),
                rx.text(State.plan_footer, size="1", color="var(--pp-ink-soft)", margin_top="14px"))


def no_plan() -> rx.Component:
    return card(rx.hstack(rx.icon("clock", size=18), rx.text("The care plan for this result is not available yet: the templates are waiting for physician approval.", size="3"),
                          spacing="2", align="center"))


def results_view() -> rx.Component:
    return rx.fragment(
        rx.grid(rx.foreach(State.risks, tier_card), columns=cols(4), spacing="4", width="100%", margin_bottom="22px"),
        rx.grid(card(title(State.current.short, " risk tier", icon="gauge"), rx.badge(State.current.tier, color_scheme=State.current.color, size="3", variant="soft"),
                     rx.progress(value=State.current.score, color_scheme=State.current.color, margin_y="24px"),
                     rx.text("Estimated risk: ", State.current.prob, size="2", class_name="pp-num"),
                     hint("Saved with the exact inputs and model version used.")),
                card(title("Main reasons behind this result", icon="list-checks"), shap_chart(),
                     rx.vstack(rx.foreach(State.reasons, lambda r: rx.text(r["sentence"], size="2")), spacing="1", margin_top="8px", align="start"),
                     rx.hstack(rx.hstack(rx.box(width="10px", height="10px", border_radius="3px", background="#2F7D5B"), rx.text("Lowers risk", size="1", color="var(--pp-ink-soft)"), spacing="1", align="center"), rx.hstack(rx.box(width="10px", height="10px", border_radius="3px", background="#B7791F"), rx.text("Raises risk", size="1", color="var(--pp-ink-soft)"), spacing="1", align="center"), justify="between", width="100%", margin_top="8px")),
                grid_template_columns=rx.breakpoints(initial="1fr", lg=".8fr 1.4fr"), gap="22px", align_items="start", width="100%"),
        rx.cond(State.consult_message != "", callout("red", "stethoscope", State.consult_message, margin_top="22px")),
        rx.heading("Your care plan", size="5", margin_top="30px", margin_bottom="14px"),
        rx.cond(State.has_plan, care_plan_card(), no_plan()),
        rx.vstack(callout("gray", "info", State.disclaimer), spacing="3", margin_top="22px", width="100%"),
        rx.hstack(rx.button(rx.icon("save", size=18), "Save report", size="3", on_click=State.save_report), justify="end", margin_top="20px", width="100%"))


def page() -> rx.Component:
    return shell(
        "Scan & assess",
        page_header("Your results", "Pick a condition to see its risk tier, the main reasons behind it and a personal care plan.", eyebrow="Step by step"),
        stepper(4),
        rx.cond(State.has_results, results_view(),
                empty_state("activity", "No assessment yet", "Fill in what is missing, then run your assessment on Scan & assess.",
                            rx.button(rx.icon("scan-line", size=16), "Go to Scan & assess", on_click=rx.redirect("/scan")))))

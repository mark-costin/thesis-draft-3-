"""Screen 7: Health Reports, Goals, and Account Data (Table, Checkbox, Button, Alert Dialog)."""
import reflex as rx

from ..insights import Insights
from reflex_google_auth import google_oauth_provider

from ..auth_ui import google_button
from ..state import GOOGLE_CLIENT_ID, Goal, Report, State
from ..ui import card, empty_state, hint, page_header, shell, title


def report_row(r: Report) -> rx.Component:
    return rx.table.row(
        rx.table.row_header_cell(r.date, white_space="nowrap"),
        rx.table.cell(rx.hstack(rx.foreach(r.summary, lambda c: rx.badge(c.label, color_scheme=c.color, variant="soft")), spacing="2", wrap="wrap"),
                      rx.text(r.plans, size="1", color="var(--pp-ink-soft)", margin_top="6px")),
        rx.table.cell(rx.hstack(rx.button(rx.icon("eye", size=14), "View results", size="2", variant="outline", on_click=State.open_results(r.assessment_id)),
                                rx.button(rx.icon("download", size=14), "Download PDF", size="2", on_click=State.download_report(r.report_id)),
                                spacing="2", justify="end", wrap="wrap"), white_space="nowrap", text_align="right"))


def goal_row(g: Goal) -> rx.Component:
    return rx.hstack(rx.checkbox(checked=g.done, on_change=lambda v: State.toggle_goal(g.goal_id, v), aria_label=g.text), rx.text(g.text, flex="1"), rx.badge(g.tag, variant="soft", color_scheme="gray"),
                     align="center", padding="12px 8px", border_top="1px solid var(--pp-border)", width="100%", class_name="pp-row")


def delete_dialog() -> rx.Component:
    """Permanent deletion (RA 10173): the API needs the password, or a fresh Google sign-in for a
    Google account, and the exact phrase DELETE MY ACCOUNT."""
    proof = rx.cond(State.auth_provider == "google",
                    rx.vstack(rx.text("Sign in with Google again to confirm it is you.", size="2"), google_button("continue_with", State.google_confirm_identity), spacing="2", width="100%"),
                    rx.input(name="password", type="password", placeholder="Your password", required=True, custom_attrs={"autoComplete": "current-password"}))
    form = rx.form(rx.vstack(proof,
                             rx.input(name="confirm", placeholder="Type DELETE MY ACCOUNT", required=True, aria_label="Type DELETE MY ACCOUNT to confirm"),
                             rx.flex(rx.alert_dialog.cancel(rx.button("Cancel", variant="soft", color_scheme="gray", type="button")),
                                     rx.button("Delete", color_scheme="red", type="submit"), spacing="3", justify="end", margin_top="8px", width="100%"),
                             spacing="3", width="100%"),
                   on_submit=State.delete_account, reset_on_submit=True)
    return rx.alert_dialog.root(
        rx.alert_dialog.trigger(rx.button(rx.icon("trash-2", size=16), "Delete account", variant="soft", color_scheme="red")),
        rx.alert_dialog.content(rx.alert_dialog.title("Delete account permanently?"),
                                rx.alert_dialog.description("This removes all your records, uploaded files and saved reports. It cannot be undone.", margin_bottom="12px"),
                                form))


def reports_table() -> rx.Component:
    heads = ["Date", "Risk summary", ""]
    table = rx.box(rx.table.root(rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in heads])),
                                 rx.table.body(rx.foreach(Insights.filtered_reports, report_row)), width="100%"), overflow_x="auto")
    none = rx.cond(State.reports.length() > 0,
                   empty_state("search-x", "No reports match your search", "Try a date, a condition such as T2D, or a tier such as High."),
                   empty_state("file-text", "No saved reports yet", "Run an assessment and choose Save Report. It will be listed here.",
                               rx.button(rx.icon("scan-line", size=16), "Start an assessment", on_click=rx.redirect("/scan"))))
    return rx.vstack(rx.input(rx.input.slot(rx.icon("search", size=16)), placeholder="Search by date, condition or tier", value=Insights.report_query, on_change=Insights.set_report_query,
                              aria_label="Search saved reports", width="100%", class_name="pp-noprint"),
                     rx.cond(Insights.filtered_reports.length() > 0, table, none), spacing="3", align="stretch", width="100%")


def goals_progress() -> rx.Component:
    return rx.box(rx.hstack(rx.text(Insights.goals_done, " of ", Insights.goals_total, " goals done", size="2", weight="bold", class_name="pp-num"),
                            rx.text(Insights.goals_pct, "%", size="2", color="var(--pp-ink-soft)", class_name="pp-num"), justify="between", width="100%"),
                  rx.progress(value=Insights.goals_pct, color_scheme="green", margin_top="8px", aria_label="Goals progress"), margin_bottom="14px", width="100%")


def page() -> rx.Component:
    content = shell(
        "Reports",
        page_header("Reports & goals", "Download your saved assessments, track the goals from your care plan and manage your account data.", eyebrow="Your records",
                    right=rx.button(rx.icon("printer", size=16), "Print this page", variant="outline", on_click=rx.call_script("window.print()"), class_name="pp-noprint")),
        rx.grid(card(title("Saved reports", icon="file-text"), reports_table()),
                card(title("My goals", icon="target"), goals_progress(),
                     rx.cond(State.goals.length() > 0, rx.vstack(rx.foreach(State.goals, goal_row), spacing="0", width="100%"),
                             empty_state("target", "No goals yet", "Add one below, or use Add as goal on your care plan.")),
                     rx.form(rx.hstack(rx.input(name="goal", placeholder="Add my own goal...", flex="1", aria_label="New goal"), rx.button(rx.icon("plus", size=16), "Add", type="submit"), margin_top="14px", width="100%"), on_submit=State.new_goal, reset_on_submit=True)),
                grid_template_columns=rx.breakpoints(initial="1fr", lg="1.8fr 1fr"), gap="22px", align_items="start", width="100%"),
        card(title("Account data", icon="database"), rx.hstack(rx.button(rx.icon("download", size=16), "Export my data", variant="outline", on_click=State.export_data), delete_dialog(),
                                                          hint("Export everything or permanently delete your account, as allowed by RA 10173."), align="center", spacing="4", wrap="wrap"), margin_top="22px"))
    # The Google button on the delete dialog needs the Google provider around the page
    return google_oauth_provider(content, client_id=GOOGLE_CLIENT_ID) if GOOGLE_CLIENT_ID else content

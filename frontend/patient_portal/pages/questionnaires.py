"""Screen 5: Questionnaires (PHQ-9, GAD-7, mMRC, PSQI, CAT, ACT) and the PHQ-9 crisis Alert Dialog.

The API scores every questionnaire; this screen only collects the answers. If PHQ-9 item 9 is
above 0, the crisis resources (NCMH 1553) appear at once, before anything is saved.
"""
import reflex as rx

from ..state import QRow, State
from ..ui import card, empty_state, hint, page_header, shell, title


def tab(t: dict) -> rx.Component:
    on = State.q_instrument == t["code"]
    return rx.button(t["title"], rx.cond(t["needed"] == "yes", rx.badge("Needed", color_scheme="amber", variant="soft", size="1")),
                     variant=rx.cond(on, "solid", "soft"), color_scheme=rx.cond(on, "teal", "gray"), size="2",
                     on_click=State.set_q_instrument(t["code"]), aria_pressed=rx.cond(on, "true", "false"))


def item_row(item: dict) -> rx.Component:
    return rx.vstack(
        rx.text(item["number"], ". ", item["text"], weight="medium"),
        rx.radio_group(State.q_options, value=item["answer"], on_change=lambda v: State.set_q_answer(item["i"], v),
                       direction=rx.cond(State.q_numeric, "row", "column"), spacing="3"),
        spacing="2", align="start", padding="14px 6px", border_top="1px solid var(--pp-border)", width="100%")


def crisis_dialog() -> rx.Component:
    hotline = lambda h: rx.hstack(rx.icon("phone", size=18), rx.link(rx.text(h["name"], ": ", h["number"], weight="bold"), href="tel:" + h["number"]),
                                  rx.text(h["hours"], size="2", color="var(--pp-ink-soft)"), spacing="2", align="center", wrap="wrap")
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(rx.hstack(rx.icon("heart-handshake", size=22, color="var(--red-10)"), rx.text("You are not alone"), spacing="2", align="center")),
            rx.alert_dialog.description(State.crisis_message, size="3", margin_bottom="14px"),
            rx.vstack(rx.foreach(State.crisis_hotlines, hotline), spacing="2", padding="14px 16px", border_radius="12px",
                      background="var(--red-2)", border="1px solid var(--red-6)", width="100%"),
            rx.flex(rx.alert_dialog.action(rx.button("I understand", size="3", on_click=State.close_crisis)), justify="end", margin_top="18px")),
        open=State.show_crisis)


def history_row(r: QRow) -> rx.Component:
    return rx.table.row(rx.table.row_header_cell(r.date, white_space="nowrap"), rx.table.cell(r.instrument), rx.table.cell(r.score, class_name="pp-num"),
                        rx.table.cell(r.band), rx.table.cell(rx.cond(r.crisis, rx.badge("Support shown", color_scheme="red", variant="soft"), "")))


def page() -> rx.Component:
    heads = ["Date", "Questionnaire", "Score", "Result", ""]
    return shell(
        "Questionnaires",
        page_header("Questionnaires", "Short check-ins about mood, anxiety, sleep and breathing. Your answers are scored by the server and feed your next assessment.",
                    eyebrow="Your check-ins"),
        rx.flex(rx.foreach(State.q_tabs, tab), gap="8px", wrap="wrap", margin_bottom="18px"),
        card(title(State.q_title, icon="clipboard-list"), rx.text(State.q_intro, size="3", color="var(--pp-ink-soft)", margin_bottom="8px"),
             rx.vstack(rx.foreach(State.q_items, item_row), spacing="0", width="100%"),
             rx.hstack(rx.button(rx.icon("send", size=16), "Save answers", size="3", on_click=State.submit_questionnaire, loading=State.q_saving),
                       rx.cond(State.q_unanswered > 0, hint(State.q_unanswered, " left to answer")), spacing="4", align="center", margin_top="20px"),
             hint("Screening only, not a diagnosis. If you ever feel unsafe, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room.", margin_top="14px")),
        rx.box(height="24px"),
        card(title("Your past answers", icon="history"),
             rx.cond(State.q_history.length() > 0,
                     rx.box(rx.table.root(rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in heads])),
                                          rx.table.body(rx.foreach(State.q_history, history_row)), width="100%"), overflow_x="auto"),
                     empty_state("clipboard-list", "No answers yet", "Pick a questionnaire above and save your answers. They will be listed here."))),
        crisis_dialog())

"""Screen 4: Family medical history. One entry per relative and diagnosis, the way the FHRS needs it:
the exact relative (so the API knows the degree and sex), the condition, the age at diagnosis,
whether a doctor confirmed it, and whether they lived in the same household. The API computes
the Family History Risk Score (FHRS) band for each disease after every change."""
import reflex as rx

from ..state import FamilyEntry, State
from ..ui import card, cols, empty_state, field, hint, page_header, shell, title


def add_form() -> rx.Component:
    return rx.form(
        rx.grid(
            field("Relative", rx.select(State.relative_options, value=State.fam_relative, on_change=State.set_fam_relative, placeholder="Select relative", width="100%"),
                  tip="Blood relatives only. Pick the exact relative: the side of the family and the relative's sex change the score."),
            field("Condition group", rx.select(State.disease_options, value=State.fam_disease, on_change=State.set_fam_disease, placeholder="Select", width="100%")),
            field("Diagnosis", rx.input(name="diagnosis", id="fam_diagnosis", placeholder="e.g. Heart attack, Type 2 diabetes, Asthma, Depression", max_length=150)),
            field("Age when diagnosed", rx.input(name="age", id="fam_age", type="number", min=0, max=120, placeholder="Leave blank if unknown"),
                  tip="An earlier age counts for more. Leave it blank if you do not know."),
            field("Confirmed by a doctor?", rx.select(["Yes", "No", "Not sure"], name="confirmed", default_value="Not sure", width="100%")),
            rx.box(rx.checkbox("We lived in the same household", name="household"), padding_top=rx.breakpoints(initial="0", lg="28px")),
            columns=cols(3), spacing="4", width="100%"),
        rx.button(rx.icon("plus", size=16), "Add relative", type="submit", size="3", margin_top="20px"),
        on_submit=State.add_family_entry, reset_on_submit=False)


def entry_row(e: FamilyEntry) -> rx.Component:
    return rx.table.row(
        rx.table.row_header_cell(e.relative), rx.table.cell(e.disease), rx.table.cell(e.diagnosis),
        rx.table.cell(rx.cond(e.age != "", e.age, "--"), class_name="pp-num"), rx.table.cell(e.onset), rx.table.cell(e.confirmed),
        rx.table.cell(rx.cond(e.household, "Yes", "No")),
        rx.table.cell(rx.icon_button(rx.icon("trash-2", size=16), variant="ghost", color_scheme="red", aria_label="Remove this entry",
                                     on_click=State.delete_family_entry(e.record_id))))


def entries_table() -> rx.Component:
    heads = ["Relative", "Group", "Diagnosis", "Age", "Onset", "Confirmed", "Same household", ""]
    table = rx.box(rx.table.root(rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in heads])),
                                 rx.table.body(rx.foreach(State.family, entry_row)), width="100%"), overflow_x="auto")
    none = empty_state("users", "No relatives added yet",
                       "Add each blood relative who had one of these conditions. If nobody did, tell us below.",
                       rx.button(rx.icon("circle-check", size=16), "No known family history", variant="outline", on_click=State.no_family_history))
    return rx.cond(State.family.length() > 0, table, none)


def band_chip(chip: dict) -> rx.Component:
    return rx.badge(chip["code"], " ", chip["band"], size="2", variant="soft", color_scheme=chip["color"], class_name="pp-num")


def page() -> rx.Component:
    return shell(
        "Family history",
        page_header("Family medical history", "Tell us about blood relatives who had heart disease or stroke, type 2 diabetes, asthma or COPD, or depression or anxiety.",
                    eyebrow="Your family"),
        card(title("Add a relative", icon="user-plus"), add_form()),
        rx.box(height="22px"),
        card(title("Your family history", icon="users"), entries_table(),
             rx.flex(rx.hstack(rx.text("Family history score (FHRS)", size="2", weight="bold"), rx.foreach(State.band_chips, band_chip), spacing="2", wrap="wrap", align="center"),
                     justify="between", align="center", gap="14px", wrap="wrap", width="100%", margin_top="18px"),
             hint("Bands from lowest to highest: Baseline, Mild, Moderate, Pronounced, Critical. The score is worked out by the server after every change.", margin_top="10px")))

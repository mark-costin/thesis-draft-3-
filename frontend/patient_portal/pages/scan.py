"""Screen 6: Scan & assess. Upload a lab result, check what OCR read, type in what it missed, confirm,
then run the assessment. Without OCR (OCR_ENGINE=none) the patient types the lab values in directly.
Only confirmed values are used by the models (the API enforces this)."""
import reflex as rx

from ..state import Check, Lab, MissingLab, State
from ..ui import card, field, hint, page_header, shell, stepper, title

UPLOAD_ID = "lab_upload"


def dropzone() -> rx.Component:
    return card(rx.upload.root(
        rx.vstack(rx.center(rx.icon("file-up", size=28), width="56px", height="56px", border_radius="50%", class_name="pp-icon-tile"),
                  rx.text("Drag and drop a laboratory result, or use your camera", weight="bold", size="4", text_align="center"),
                  rx.text("JPEG, PNG or PDF, up to 10 MB. Photos lose their location and camera details before they are stored.", color="var(--pp-ink-soft)", size="2", text_align="center"),
                  rx.hstack(rx.button(rx.icon("upload", size=16), "Upload file", type="button", size="3", loading=State.uploading),
                            rx.cond(State.doc_status != "", rx.badge(State.doc_status_label, variant="soft", size="2")),
                            align="center", spacing="3", wrap="wrap", justify="center"),
                  align="center", spacing="3"),
        id=UPLOAD_ID, accept={"application/pdf": [".pdf"], "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"]}, max_files=1, max_size=10 * 1024 * 1024,
        on_drop=State.handle_upload(rx.upload_files(upload_id=UPLOAD_ID)),  # picking a file triggers this too; on a phone it offers the camera
        border="1.5px dashed var(--pp-border-strong)", border_radius="16px", background="var(--pp-surface-muted)", padding=rx.breakpoints(initial="24px 16px", md="32px"), width="100%", class_name="pp-dropzone"),
        rx.cond(State.doc_error != "", rx.callout.root(rx.callout.icon(rx.icon("info")), rx.callout.text(State.doc_error), margin_top="12px")),
        padding=rx.breakpoints(initial="14px", md="18px"))


def original_panel() -> rx.Component:
    preview = rx.cond(State.doc_preview != "", rx.image(src=State.doc_preview, max_height="420px", width="100%", object_fit="contain", alt="Your uploaded lab result"),
                      rx.center(rx.vstack(rx.icon("file-text", size=40, color="var(--pp-ink-soft)"),
                                          rx.text(rx.cond(State.doc_is_pdf, "PDF files are not previewed here. Keep the paper or file open beside this page.", "No preview"), size="2", color="var(--pp-ink-soft)", text_align="center"),
                                          align="center"), height="320px", width="100%"))
    return card(title("Original document", icon="file-search"), rx.box(preview, class_name="pp-panel", padding="12px", min_height="340px"))


def lab_row(l: Lab) -> rx.Component:
    """One value OCR read: fix the number or unit as printed on the report, or remove the line."""
    return rx.form(
        rx.grid(rx.vstack(rx.text(l.label, weight="medium"), rx.cond(l.reported != "", rx.text("Report said ", l.reported, size="1", color="var(--pp-ink-soft)")), spacing="0"),
                rx.hstack(rx.input(name="value", default_value=l.value, width="100px", aria_label=l.label), rx.input(name="unit", default_value=l.unit, width="110px", aria_label="Unit"), spacing="2"),
                rx.hstack(rx.cond(l.flagged, rx.badge("Check this", color_scheme="amber", variant="soft")),
                          rx.badge(l.source, variant="soft", color_scheme=rx.cond(l.source == "Scanned", "teal", "gray")),
                          rx.icon_button(rx.icon("check", size=16), type="submit", variant="soft", aria_label="Save this value"),
                          rx.icon_button(rx.icon("trash-2", size=16), type="button", variant="ghost", color_scheme="red", aria_label="Remove this line", on_click=State.remove_lab(l.lab_id)),
                          spacing="2", align="center"),
                grid_template_columns=rx.breakpoints(initial="1fr", md="1.3fr 1.4fr auto"), align_items="center", gap="10px 14px", width="100%"),
        on_submit=lambda form: State.fix_lab(l.lab_id, form), reset_on_submit=False,
        padding="11px 6px", border_top="1px solid var(--pp-border)", width="100%")


def missing_lab_row(m: MissingLab) -> rx.Component:
    return field(m.label, rx.hstack(rx.input(name="lab_" + m.code, type="number", step="any", placeholder="Value", width="140px", aria_label=m.label),
                                    rx.text(m.unit, size="2", color="var(--pp-ink-soft)"), align="center", spacing="2"))


def missing_labs_grid() -> rx.Component:
    return rx.cond(State.missing_labs.length() > 0,
                   rx.vstack(rx.text("Lab values that improve your assessment", weight="bold", size="3"),
                             hint("Optional. Type only the ones on your report, in the unit shown. Leave the rest blank."),
                             rx.grid(rx.foreach(State.missing_labs, missing_lab_row), columns=rx.breakpoints(initial="1", sm="2", lg="3"), spacing="4", width="100%"),
                             field("Date on the report", rx.input(name="collected_on", id="lab_collected_on", type="date"), tip="The date the sample was taken. Leave blank if unknown."),
                             spacing="3", width="100%", margin_top="16px"))


def review_card() -> rx.Component:
    """OCR finished: the values it read, plus boxes for the required labs it did not find."""
    return card(title("Check the values we read", icon="flask-conical"),
                rx.cond(State.labs.length() > 0, rx.foreach(State.labs, lab_row),
                        hint("No values could be read from this document. Type them in below.")),
                rx.form(missing_labs_grid(),
                        rx.hstack(rx.button(rx.icon("circle-check", size=18), "Confirm all values", type="submit", size="3"),
                                  rx.button("Not my result / wrong file", type="button", variant="soft", color_scheme="gray", on_click=State.reject_document),
                                  spacing="3", wrap="wrap", margin_top="20px"),
                        on_submit=State.confirm_document, reset_on_submit=True),
                hint("Values you have not confirmed are never used for risk prediction.", margin_top="12px"))


def typed_labs_card() -> rx.Component:
    """No document in review (or OCR is off): type the lab values in."""
    return card(title("Type your lab values", icon="keyboard"),
                rx.cond(State.missing_labs.length() > 0,
                        rx.form(missing_labs_grid(), rx.button(rx.icon("save", size=16), "Save lab values", type="submit", size="3", margin_top="20px"),
                                on_submit=State.save_typed_labs, reset_on_submit=True),
                        rx.hstack(rx.icon("circle-check", size=18, color="var(--green-10)"), rx.text("Every lab value the models use is saved.", size="2"), spacing="2", align="center")))


def other_row(c: Check) -> rx.Component:
    return rx.hstack(rx.icon("circle-alert", size=16, color="var(--amber-10)"), rx.text(c.label, flex="1"), rx.link(c.where, href=c.href, size="2", weight="bold"),
                     align="center", padding="10px 6px", border_top="1px solid var(--pp-border)", width="100%")


def page() -> rx.Component:
    return shell(
        "Scan & assess",
        page_header("Scan & assess", "Upload a lab result, check every value, fill in what is missing, then run your assessment.", eyebrow="Step by step"),
        stepper(2),
        dropzone(),
        rx.cond(State.in_review,
                rx.grid(original_panel(), review_card(), grid_template_columns=rx.breakpoints(initial="1fr", lg="1fr 1.2fr"), gap="22px", align_items="start", margin_top="22px", width="100%"),
                rx.box(typed_labs_card(), margin_top="22px")),
        rx.grid(card(title("Other information still needed", icon="clipboard-list"),
                     rx.cond(State.other_missing.length() > 0, rx.foreach(State.other_missing, other_row),
                             rx.hstack(rx.icon("circle-check", size=18, color="var(--green-10)"), rx.text("Nothing else is missing.", size="2"), spacing="2", align="center"))),
                card(rx.callout.root(rx.callout.icon(rx.icon("info")), rx.callout.text("The assessment uses only confirmed values. It is a screening, not a diagnosis.")),
                     rx.button(rx.icon("activity", size=18), "Run my assessment", size="3", width="100%", margin_top="18px", disabled=~State.can_run, loading=State.running, on_click=State.run_assessment),
                     rx.text(State.run_hint, size="2", color="var(--pp-ink-soft)", margin_top="10px", aria_live="polite")),
                grid_template_columns=rx.breakpoints(initial="1fr", lg="1fr 1fr"), gap="22px", margin_top="22px", width="100%"))

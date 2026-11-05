"""Screen 5: Document Scan, Verification, and Missing Information (Upload, two-column Grid, Input and Badge, Form, Callout, Button)."""
import reflex as rx

from ..state import Lab, State
from ..ui import card, field, hint, page_header, shell, stepper, title

UPLOAD_ID = "lab_upload"


def dropzone() -> rx.Component:
    return card(rx.upload.root(
        rx.vstack(rx.center(rx.icon("file-up", size=28), width="56px", height="56px", border_radius="50%", class_name="pp-icon-tile"),
                  rx.text("Drag and drop a laboratory result, or use your camera", weight="bold", size="4", text_align="center"),
                  rx.text("JPEG, PNG or PDF, up to 10 MB", color="var(--pp-ink-soft)", size="2"),
                  rx.hstack(rx.button(rx.icon("upload", size=16), "Upload file", type="button", size="3"), rx.button(rx.icon("camera", size=16), "Scan photo", type="button", variant="outline", size="3"),
                            rx.badge(State.doc_status, variant="soft", size="2"), align="center", spacing="3", wrap="wrap", justify="center"),
                  align="center", spacing="3"),
        id=UPLOAD_ID, accept={"application/pdf": [".pdf"], "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"]}, max_files=1, max_size=10 * 1024 * 1024,
        on_drop=State.handle_upload(rx.upload_files(upload_id=UPLOAD_ID)),  # picking a file triggers this too; on a phone it offers the camera
        border="1.5px dashed var(--pp-border-strong)", border_radius="16px", background="var(--pp-surface-muted)", padding=rx.breakpoints(initial="24px 16px", md="32px"), width="100%", class_name="pp-dropzone"), padding=rx.breakpoints(initial="14px", md="18px"))


def original_panel() -> rx.Component:
    preview = rx.cond(State.is_image, rx.image(src=rx.get_upload_url(State.doc_name), max_height="420px", width="100%", object_fit="contain"),
                      rx.center(rx.vstack(rx.icon("file-text", size=40, color="var(--pp-ink-soft)"), rx.text("Preview not available for PDF in this mock-up", size="2", color="var(--pp-ink-soft)"), align="center"),
                                height="320px", width="100%"))
    return card(title("Original document", icon="file-search"), rx.text(State.doc_name, size="2", color="var(--pp-ink-soft)", margin_bottom="10px", word_break="break-all"),
                rx.box(preview, class_name="pp-panel", padding="12px", min_height="340px"))


def lab_row(l: Lab, i: int) -> rx.Component:
    return rx.grid(rx.text(l.label), rx.hstack(rx.input(value=l.value, placeholder="Enter value", on_change=lambda v: State.set_lab(i, v), width="110px", aria_label=l.label), rx.text(l.unit, size="2", color="var(--pp-ink-soft)"), align="center"),
                   rx.badge(l.source, variant="soft", color_scheme=rx.cond(l.source == "Scanned", "teal", "gray")),
                   grid_template_columns=rx.breakpoints(initial="1fr auto", sm="1.2fr 1.2fr auto"), align_items="center", gap="10px 14px", padding="11px 6px", border_top="1px solid var(--pp-border)", width="100%", class_name="pp-row")


def page() -> rx.Component:
    return shell(
        "Scan & assess",
        page_header("Scan & assess", "Upload a lab result, check every extracted value, fill in what is missing, then run your assessment.", eyebrow="Step by step"),
        stepper(2),
        dropzone(),
        rx.grid(original_panel(), card(title("Extracted values", icon="flask-conical"), rx.foreach(State.labs, lab_row),
                                       hint("Every value is editable. Confirm or correct each one.", margin_top="14px")),
                grid_template_columns=rx.breakpoints(initial="1fr", lg="1fr 1fr"), gap="22px", align_items="start", margin_top="22px", width="100%"),
        rx.grid(card(title("Missing information", icon="clipboard-list"),
                     rx.grid(field("Smoking status", rx.select(["Never smoked", "Former smoker", "Current smoker"], value=State.smoking, on_change=State.set_smoking, placeholder="Select", width="100%")),
                             field("Waist circumference (cm)", rx.input(value=State.waist, on_change=State.set_waist, type="number")), columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%"),
                     rx.cond(State.auth_provider == "google", rx.grid(
                         field("Date of birth", rx.input(value=State.dob, on_change=State.set_dob, type="date")),
                         field("Gender", rx.select(["Female", "Male"], value=State.sex, on_change=State.set_sex, placeholder="Select", width="100%")),
                         columns=rx.breakpoints(initial="1", sm="2"), spacing="4", width="100%", margin_top="16px"))),
                card(rx.callout.root(rx.callout.icon(rx.icon("info")), rx.callout.text("Values you have not confirmed are not used for risk prediction.")),
                     rx.button(rx.icon("circle-check", size=18), "Confirm values and run assessment", size="3", width="100%", margin_top="18px", disabled=~State.can_run, on_click=State.run_assessment),
                     rx.text(State.run_hint, size="2", color="var(--pp-ink-soft)", margin_top="10px", aria_live="polite")),
                grid_template_columns=rx.breakpoints(initial="1fr", lg="1fr 1fr"), gap="22px", margin_top="22px", width="100%"))

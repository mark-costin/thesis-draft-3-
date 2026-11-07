"""Screen 3: Vitals, Lifestyle, and Health History Log (Tabs, Input and Select, Radio Group, Table)."""
import reflex as rx

from ..state import Q, State, VitalsRow
from ..ui import card, cols, empty_state, field, hint, page_header, shell, title


def num(name: str, ph: str, **kw) -> rx.Component:
    return rx.input(name=name, type="number", step="any", placeholder=ph, **kw)


def vitals_form() -> rx.Component:
    return rx.form(
        rx.grid(field("Systolic BP (mmHg)", num("systolic", "120", min=70, max=250), tip="The top number of a blood pressure reading."), field("Diastolic BP (mmHg)", num("diastolic", "80", min=40, max=150), tip="The bottom number of a blood pressure reading."), field("Heart rate (bpm)", num("hr", "72", min=30, max=220)),
                field("SpO2 (%)", num("spo2", "98", min=70, max=100), tip="Oxygen saturation: the share of oxygen carried by your blood, read with a finger clip. 95% or higher is usual."), field("Weight (kg)", rx.input(value=State.weight, on_change=State.set_weight, type="number", step="0.1", min=20, max=300)),
                field("Height (cm)", rx.input(value=State.height, on_change=State.set_height, type="number", step="0.1", min=100, max=250)),
                field("Waist circumference (cm)", rx.input(name="waist", value=State.waist, on_change=State.set_waist, type="number", step="0.1", min=40, max=200, placeholder="82"), tip="Measure around your waist at the level of your belly button, after breathing out."),
                field("Home blood glucose (mg/dL)", num("glucose", "92", min=20, max=600), tip="A finger-prick reading from your home meter."),
                field("When was the blood sugar taken?", rx.select(["Fasting", "Random", "2 hours after a meal"], name="glucose_context", placeholder="Select", width="100%")),
                columns=cols(4), spacing="4", width="100%"),
        hint("Enter only what you measured today. Blood pressure needs both numbers.", margin_top="12px"),
        rx.flex(rx.badge(rx.icon("calculator", size=14), "BMI ", State.bmi, " (worked out for you)", size="2", variant="soft", class_name="pp-num"),
                rx.button(rx.icon("save", size=16), "Save vitals", type="submit", size="3"),
                justify="between", align="center", gap="12px", wrap="wrap", margin_top="24px", width="100%"), on_submit=State.save_vitals, reset_on_submit=False)


def lifestyle_form() -> rx.Component:
    return rx.form(
        rx.grid(field("Smoking status", rx.select(["Never smoked", "Former smoker", "Current smoker"], name="smoking", value=State.smoking, on_change=State.set_smoking, placeholder="Select", width="100%")),
                field("Pack-years", num("pack_years", "0", default_value=State.pack_years, min=0, max=200, disabled=State.smoking == "Never smoked"), tip="Packs smoked per day times years smoked. Example: 1 pack a day for 10 years is 10 pack-years."),
                field("Physical activity (minutes a week)", num("activity", "150", default_value=State.activity, min=0, max=10080), tip="Minutes of moderate activity (brisk walking, cycling) in a usual week. 30 minutes on 5 days is 150."),
                field("Sleep (hours a day)", num("sleep", "7", default_value=State.sleep, min=0, max=24)),
                field("Sitting time (hours a day)", num("sedentary", "6", default_value=State.sedentary, min=0, max=24)),
                field("Dust, smoke or fumes at work", rx.select(["Yes", "No"], name="dust", default_value=State.dust, placeholder="Select", width="100%"), tip="Regular exposure at work, such as construction dust, farming, mining or factory fumes."),
                field("Alcohol or other substance use", rx.select(["None", "Past", "Current"], name="alcohol", default_value=State.alcohol, placeholder="Select", width="100%")),
                field("Daily sodium (mg), if known", num("sodium", "2000", default_value=State.sodium, min=0, max=20000), tip="Salt intake counted as sodium. Leave blank if you are not sure."),
                field("Daily carbohydrates (g), if known", num("carbs", "250", default_value=State.carbs, min=0, max=1500), tip="Leave blank if you are not sure."),
                columns=cols(3), spacing="4", width="100%"),
        rx.box(rx.text("Optional lab results that tailor your care plan", size="3", weight="bold"), hint("Skip anything you do not have. They are saved as lab values you typed in."), margin_top="26px", margin_bottom="12px", padding_top="20px", border_top="1px solid var(--pp-border)"),
        rx.grid(field("TSH (mIU/L)", num("tsh", "2.0", default_value=State.tsh), tip="Thyroid-stimulating hormone: a blood test that checks how your thyroid is working."),
                field("Vitamin B12 (pg/mL)", num("b12", "400", default_value=State.b12), tip="Vitamin B12 level from a blood test."), columns=cols(3), spacing="4", width="100%"),
        rx.button(rx.icon("save", size=16), "Save lifestyle", type="submit", size="3", margin_top="24px"), on_submit=State.save_lifestyle, reset_on_submit=False)


def history_row(q: Q, i: int) -> rx.Component:
    return rx.hstack(rx.text(q.question, flex="1"), rx.radio_group(["Yes", "No", "Not sure"], value=q.answer, direction="row", spacing="4", on_change=lambda v: State.set_history(i, v)),
                     align="center", padding="14px 8px", border_top="1px solid var(--pp-border)", width="100%", wrap="wrap", class_name="pp-row")


def vitals_row(r: VitalsRow) -> rx.Component:
    return rx.table.row(rx.table.row_header_cell(r.date), rx.table.cell(r.bp), rx.table.cell(r.hr), rx.table.cell(r.weight), rx.table.cell(r.bmi), rx.table.cell(r.glucose), rx.table.cell(r.spo2))


def page() -> rx.Component:
    heads = ["Date", "BP (mmHg)", "HR (bpm)", "Weight (kg)", "BMI", "Glucose (mg/dL)", "SpO2 (%)"]
    return shell(
        "Vitals & lifestyle",
        page_header("Vitals & lifestyle", "Log today's readings, update your habits and review your health history. Saved entries feed your next assessment.", eyebrow="Your daily log"),
        card(rx.tabs.root(rx.tabs.list(rx.tabs.trigger(rx.hstack(rx.icon("heart-pulse", size=16), "Vitals", spacing="2", align="center"), value="vitals"), rx.tabs.trigger(rx.hstack(rx.icon("activity", size=16), "Lifestyle", spacing="2", align="center"), value="lifestyle"), rx.tabs.trigger(rx.hstack(rx.icon("clipboard-list", size=16), "Health history", spacing="2", align="center"), value="history")),
                          rx.tabs.content(vitals_form(), value="vitals", padding_top="20px"), rx.tabs.content(lifestyle_form(), value="lifestyle", padding_top="20px"),
                          rx.tabs.content(rx.vstack(rx.foreach(State.history, history_row), spacing="0", width="100%"),
                                          rx.button(rx.icon("save", size=16), "Save health history", size="3", margin_top="20px", on_click=State.save_history),
                                          value="history", padding_top="12px"),
                          default_value="vitals", width="100%")),
        rx.box(height="24px"),
        card(title("History of previous entries", icon="history"),
             rx.cond(State.vitals_history.length() > 0,
                     rx.box(rx.table.root(rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in heads])), rx.table.body(rx.foreach(State.vitals_history, vitals_row)), width="100%"), overflow_x="auto"),
                     empty_state("heart-pulse", "No entries yet", "Save your first vitals entry above and it will appear here with its date.")),
             padding=rx.breakpoints(initial="20px", md="24px 26px")))

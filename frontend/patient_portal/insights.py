"""Display-only derived values for the UI: normal-range flags, trends, reminder counts, report search, goal progress.

``Insights`` is a sub-state of ``State``: it only READS State's data and never changes it.
The ranges below are general reference ranges for display. Have the clinical team confirm them before release.
"""
import reflex as rx

from .state import Report, State


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _trend(cur, prev, dp: int = 1) -> tuple[str, str]:
    if cur is None or prev is None:
        return "flat", ""
    d = cur - prev
    if abs(d) < 10 ** -(dp + 1):
        return "flat", "no change"
    return ("up" if d > 0 else "down"), f"{d:+.{dp}f}"


def _flag(label: str, color: str, trend: str, delta: str) -> dict[str, str]:
    return {"label": label, "color": color, "trend": trend, "delta": delta}


class Insights(State):
    report_query: str = ""

    @rx.event
    def set_report_query(self, value: str):
        self.report_query = value

    # ---- vitals: in-range label + change since the previous entry --------------------------------
    @rx.var
    def vital_flags(self) -> dict[str, dict[str, str]]:
        hist = self.vitals_history
        cur = hist[0] if hist else None
        prev = hist[1] if len(hist) > 1 else None
        none = _flag("No data", "gray", "flat", "")
        if cur is None:
            return {"bp": none, "hr": none, "bmi": none, "spo2": none}

        def bp(row):
            try:
                s, d = row.bp.split("/")
                return float(s), float(d)
            except (AttributeError, ValueError):
                return None, None

        sys_, dia = bp(cur)
        psys, _ = bp(prev) if prev else (None, None)
        if sys_ is None:
            bp_flag = none
        else:
            t, dl = _trend(sys_, psys, 0)
            if sys_ < 90 or dia < 60:
                bp_flag = _flag("Below range", "amber", t, dl)
            elif sys_ < 120 and dia < 80:
                bp_flag = _flag("In range", "green", t, dl)
            elif sys_ < 130 and dia < 80:
                bp_flag = _flag("Slightly above", "amber", t, dl)
            else:
                bp_flag = _flag("Above range", "red", t, dl)

        hr = _num(cur.hr)
        t, dl = _trend(hr, _num(prev.hr) if prev else None, 0)
        hr_flag = none if hr is None else _flag("In range" if 60 <= hr <= 100 else ("Below range" if hr < 60 else "Above range"), "green" if 60 <= hr <= 100 else "amber", t, dl)

        bmi = _num(cur.bmi)
        t, dl = _trend(bmi, _num(prev.bmi) if prev else None, 1)
        if bmi is None:
            bmi_flag = none
        elif bmi < 18.5:
            bmi_flag = _flag("Below range", "amber", t, dl)
        elif bmi < 25:
            bmi_flag = _flag("In range", "green", t, dl)
        elif bmi < 30:
            bmi_flag = _flag("Above range", "amber", t, dl)
        else:
            bmi_flag = _flag("Above range", "red", t, dl)

        sp = _num(cur.spo2)
        t, dl = _trend(sp, _num(prev.spo2) if prev else None, 0)
        if sp is None:
            sp_flag = none
        elif sp >= 95:
            sp_flag = _flag("In range", "green", t, dl)
        elif sp >= 90:
            sp_flag = _flag("Below range", "amber", t, dl)
        else:
            sp_flag = _flag("Below range", "red", t, dl)
        return {"bp": bp_flag, "hr": hr_flag, "bmi": bmi_flag, "spo2": sp_flag}

    # ---- reminders (notification bell) -----------------------------------------------------------
    @rx.var
    def missing_items(self) -> list[str]:
        return [c.label for c in self.checklist if not c.done]

    @rx.var
    def missing_count(self) -> int:
        return len([c for c in self.checklist if not c.done])

    # ---- goals progress --------------------------------------------------------------------------
    @rx.var
    def goals_total(self) -> int:
        return len(self.goals)

    @rx.var
    def goals_done(self) -> int:
        return len([g for g in self.goals if g.done])

    @rx.var
    def goals_pct(self) -> int:
        return round(100 * self.goals_done / self.goals_total) if self.goals_total else 0

    # ---- reports search --------------------------------------------------------------------------
    @rx.var
    def filtered_reports(self) -> list[Report]:
        q = self.report_query.strip().lower()
        if not q:
            return self.reports
        return [r for r in self.reports if q in r.date.lower() or q in r.plans.lower() or any(q in c.label.lower() for c in r.summary)]

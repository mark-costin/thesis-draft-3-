"""Care plan engine for careplan_2026-10-27.

The model's probability sets the tier; the tier picks one of 12 physician-reviewed templates per disease.
Add-on items show only when their trigger is true in the patient's saved data. The AI never writes the advice.
Templates live in data/careplan_templates.json (rebuild it from the .docx with tools/parse_careplan.py).
"""
import json
from pathlib import Path
from typing import Any, Callable

_DATA = json.loads((Path(__file__).parent / "data" / "careplan_templates.json").read_text(encoding="utf-8"))
TEMPLATES: dict[str, dict] = _DATA["templates"]
TEMPLATE_VERSION: str = _DATA["version"]

DISCLAIMER = "This care plan is for educational screening only and is not a medical diagnosis. Please consult a healthcare provider for medical advice."
LOW_MAX, HIGH_MIN = 0.20, 0.50  # CDSS Data Dictionary tiers: Low < 0.20, Moderate 0.20 to < 0.50, High >= 0.50
BMI_TRIGGER = 25.0  # T2D add-on A6. Open question for the physician: 25 (Data Dictionary) or 23 (ADA Asian cutoff).

# Physician sign-off. A template stays "pending" until the reviewer and date are filled in (careplan section 7).
REVIEW: dict[str, dict[str, str]] = {}  # e.g. {"CVD-LOW": {"by": "Dr. Santos", "on": "2026-11-05"}}

ALERT_CATEGORIES = ("Urgent warning signs", "Safety")


def tier_for(prob: float) -> str:
    return "Low" if prob < LOW_MAX else "Moderate" if prob < HIGH_MIN else "High"


def template_id(disease: str, tier: str) -> str:
    return f"{disease}-{tier.upper()}"


# ---- family history bands (1 to 4) -------------------------------------------------
# Plan default: the careplan only says "band 3 or 4"; the exact scoring below is a placeholder until the CDSS Data Dictionary rule is confirmed.
_DEGREE_W = {1: 2.0, 2: 1.0, 3: 0.5}
_AGE_W = {"Under 40": 2.0, "40 to 54": 1.5, "55 to 64": 1.0, "65 or older": 0.5, "": 1.0}


def family_band(disease_key: str, groups: dict[int, list[Any]]) -> int:
    """disease_key is cvd/t2d/crd/neuro; groups maps degree -> list of Fam rows."""
    pts = sum(_DEGREE_W[deg] * _AGE_W.get(r.age, 1.0) for deg, rows in groups.items() for r in rows if getattr(r, disease_key))
    return 1 if pts == 0 else 2 if pts < 2 else 3 if pts < 4 else 4


# ---- add-on triggers ---------------------------------------------------------------
def _n(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gt(v, x): return (n := _n(v)) is not None and n > x
def _ge(v, x): return (n := _n(v)) is not None and n >= x
def _lt(v, x): return (n := _n(v)) is not None and n < x


TRIGGERS: dict[str, dict[str, Callable[[dict], bool]]] = {
    "CVD": {"A1": lambda f: f["smoker"], "A2": lambda f: f["band"]["CVD"] >= 3, "A3": lambda f: _gt(f["sodium_mg"], 2300)},
    "T2D": {"A1": lambda f: f["band"]["T2D"] >= 3, "A2": lambda f: f["gestational_dm"], "A3": lambda f: f["acanthosis"], "A4": lambda f: f["smoker"],
            "A5": lambda f: _ge(f["sedentary_hours"], 8), "A6": lambda f: _ge(f["bmi"], BMI_TRIGGER)},
    "CRD": {"A1": lambda f: f["smoker"], "A2": lambda f: f["dust"], "A3": lambda f: f["rhinitis"] or f["childhood_asthma"] or f["band"]["CRD"] >= 3},
    "NEURO": {"A1": lambda f: _lt(f["sleep_hours"], 7) or _gt(f["sleep_hours"], 9), "A2": lambda f: f["alcohol_current"], "A3": lambda f: _gt(f["psqi"], 5),
              "A4": lambda f: _lt(f["tsh"], 0.34) or _gt(f["tsh"], 4.25) or _lt(f["b12"], 200)},
}
# Every add-on in the document must have a rule, or the app fails at start-up instead of silently hiding advice.
for _t in TEMPLATES.values():
    for _a in _t["addons"]:
        assert _a["id"] in TRIGGERS[_t["disease"]], f"No trigger rule for {_t['disease']} {_a['id']}"


def build_plan(disease: str, prob: float, facts: dict, goal_texts: set[str] | None = None) -> dict[str, Any]:
    """Return the selected template with the add-ons that apply, as plain dicts for the UI."""
    tier = tier_for(prob)
    tid = template_id(disease, tier)
    t = TEMPLATES[tid]
    goal_texts = goal_texts or set()
    rows = [dict(i, addon=False, label=i["category"]) for i in t["items"]]
    rows += [dict(a, addon=True, category=_addon_category(a), label=_addon_category(a)) for a in t["addons"] if TRIGGERS[disease][a["id"]](facts)]
    for r in rows:
        r["basis_color"] = "amber" if "Plan default" in r["basis"] else "gray"
        r["in_goals"] = r["text"] in goal_texts
        r["goal"] = bool(r["goal"])
    r_info = REVIEW.get(tid, {})
    return {"id": tid, "tier": tier, "version": TEMPLATE_VERSION, "reviewed_by": r_info.get("by", ""), "reviewed_on": r_info.get("on", ""),
            "actions": [r for r in rows if r["category"] not in ALERT_CATEGORIES], "alerts": [r for r in rows if r["category"] in ALERT_CATEGORIES]}


def _addon_category(a: dict) -> str:
    """Add-ons carry no category column in the document; infer it from the wording."""
    text = a["text"].lower()
    return "Check-up and tests" if any(k in text for k in ("test", "screening", "result to your doctor", "ask your doctor")) else "Lifestyle"

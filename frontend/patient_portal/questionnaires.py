"""The questionnaires on Screen 5. Item order and answer ranges match backend/questionnaires.py,
which scores them (the screen never computes a total).

Wording and licences:
- PHQ-9 and GAD-7: Spitzer, Williams, Kroenke and colleagues, with an educational grant from
  Pfizer Inc. No permission required to reproduce, translate, display or distribute.
- mMRC dyspnoea scale: the modified Medical Research Council grades, freely used.
- CAT (COPD Assessment Test, GSK), ACT (Asthma Control Test, QualityMetric/GSK) and PSQI
  (University of Pittsburgh) are copyrighted. The screen shows only a short topic per item and the
  number scale. TODO (group): get permission or a licence, then paste the official item wording
  into "text" below. Until then the patient answers with a paper or official copy at hand.
"""

FREQUENCY = ["Not at all", "Several days", "More than half the days", "Nearly every day"]  # 0-3

INSTRUMENTS = {
    "PHQ9": {
        "title": "PHQ-9 (mood)",
        "intro": "Over the last 2 weeks, how often have you been bothered by any of the following problems?",
        "low": 0,
        "options": FREQUENCY,
        "items": [
            "Little interest or pleasure in doing things",
            "Feeling down, depressed, or hopeless",
            "Trouble falling or staying asleep, or sleeping too much",
            "Feeling tired or having little energy",
            "Poor appetite or overeating",
            "Feeling bad about yourself, or that you are a failure, or have let yourself or your family down",
            "Trouble concentrating on things, such as reading the newspaper or watching television",
            "Moving or speaking so slowly that other people could have noticed. Or the opposite: being so fidgety or restless that you have been moving around a lot more than usual",
            "Thoughts that you would be better off dead, or of hurting yourself in some way",
        ],
    },
    "GAD7": {
        "title": "GAD-7 (anxiety)",
        "intro": "Over the last 2 weeks, how often have you been bothered by the following problems?",
        "low": 0,
        "options": FREQUENCY,
        "items": [
            "Feeling nervous, anxious, or on edge",
            "Not being able to stop or control worrying",
            "Worrying too much about different things",
            "Trouble relaxing",
            "Being so restless that it is hard to sit still",
            "Becoming easily annoyed or irritable",
            "Feeling afraid, as if something awful might happen",
        ],
    },
    "MMRC": {
        "title": "mMRC (breathlessness)",
        "intro": "Choose the one sentence that describes you best.",
        "low": 0,
        "options": [
            "Grade 0: I only get breathless with strenuous exercise",
            "Grade 1: I get short of breath when hurrying on level ground or walking up a slight hill",
            "Grade 2: On level ground, I walk slower than people of my age because of breathlessness, or I have to stop for breath when walking at my own pace",
            "Grade 3: I stop for breath after walking about 100 metres or after a few minutes on level ground",
            "Grade 4: I am too breathless to leave the house, or I am breathless when dressing",
        ],
        "items": ["Breathlessness"],
    },
    "PSQI": {
        "title": "PSQI (sleep quality)",
        "intro": "Enter the 7 component scores of your Pittsburgh Sleep Quality Index (each 0 to 3). 0 means no difficulty, 3 means severe difficulty.",
        "low": 0,
        "options": ["0", "1", "2", "3"],
        "items": ["Subjective sleep quality", "Sleep latency (time to fall asleep)", "Sleep duration", "Habitual sleep efficiency",
                  "Sleep disturbances", "Use of sleeping medication", "Daytime dysfunction"],
    },
    "CAT": {
        "title": "CAT (COPD)",
        "intro": "For each topic, pick a number from 0 (no problem) to 5 (the worst). Use the official COPD Assessment Test wording.",
        "low": 0,
        "options": ["0", "1", "2", "3", "4", "5"],
        "items": ["Cough", "Phlegm (mucus) in the chest", "Chest tightness", "Breathlessness walking up a hill or one flight of stairs",
                  "Limits on activities at home", "Confidence leaving home", "Sleep", "Energy"],
    },
    "ACT": {
        "title": "ACT (asthma control)",
        "intro": "For each topic, pick a number from 1 (the worst) to 5 (the best), thinking of the past 4 weeks. Use the official Asthma Control Test wording.",
        "low": 1,
        "options": ["1", "2", "3", "4", "5"],
        "items": ["Asthma keeping you from work, school or home tasks", "Shortness of breath", "Waking at night or early because of asthma",
                  "Use of a rescue inhaler or nebulizer", "How well your asthma has been controlled"],
    },
}

ORDER = ["PHQ9", "GAD7", "MMRC", "PSQI", "CAT", "ACT"]
PHQ9_SELF_HARM_ITEM = 8  # zero-based, same as backend/questionnaires.py

# TODO(safety): same text and number as CRISIS_RESOURCES in backend/questionnaires.py. Shown at
# once when PHQ-9 item 9 is above 0 (before saving); after saving, the API's own text is shown.
CRISIS_MESSAGE = ("You said you have had thoughts of hurting yourself. You are not alone, and help is "
                  "available right now. If you are in danger, go to the nearest emergency room.")
CRISIS_HOTLINES = [{"name": "NCMH Crisis Hotline", "number": "1553", "hours": "24/7, free from landline and mobile"}]

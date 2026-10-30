"""Scoring for the self-administered questionnaires (11_questionnaire_responses.sql).

The server always computes the total from the item answers; a total sent by the client is never
trusted. Bands are the instruments' published screening cut-offs. They describe a score, they
are not a diagnosis.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    name: str
    items: int        # number of answers expected, in question order
    low: int          # lowest allowed answer per item
    high: int         # highest allowed answer per item
    bands: tuple      # (upper score limit, label), checked in order


INSTRUMENTS = {
    # PHQ-9: 9 items, 0-3 each, total 0-27. Item 9 = thoughts of self-harm.
    "PHQ9": Instrument("PHQ-9", 9, 0, 3, ((4, "minimal"), (9, "mild"), (14, "moderate"),
                                          (19, "moderately severe"), (27, "severe"))),
    # GAD-7: 7 items, 0-3 each, total 0-21
    "GAD7": Instrument("GAD-7", 7, 0, 3, ((4, "minimal"), (9, "mild"), (14, "moderate"), (21, "severe"))),
    # PSQI: the 7 component scores (0-3 each), total 0-21. The screen turns the 19 raw
    # questions into components; above 5 means poor sleep quality.
    "PSQI": Instrument("PSQI", 7, 0, 3, ((5, "good sleep quality"), (21, "poor sleep quality"))),
    # mMRC dyspnea scale: one answer, grade 0-4
    "MMRC": Instrument("mMRC", 1, 0, 4, ((0, "grade 0"), (1, "grade 1"), (2, "grade 2"),
                                         (3, "grade 3"), (4, "grade 4"))),
    # CAT: 8 items, 0-5 each, total 0-40
    "CAT": Instrument("CAT", 8, 0, 5, ((9, "low impact"), (20, "medium impact"), (30, "high impact"),
                                       (40, "very high impact"))),
    # ACT: 5 items, 1-5 each, total 5-25
    "ACT": Instrument("ACT", 5, 1, 5, ((15, "very poorly controlled"), (19, "not well controlled"),
                                       (25, "well controlled"))),
}

PHQ9_SELF_HARM_ITEM = 8  # zero-based position of PHQ-9 item 9

CRISIS_RESOURCES = {
    "message": ("You said you have had thoughts of hurting yourself. You are not alone, and help is "
                "available right now. If you are in danger, go to the nearest emergency room."),
    "hotlines": [
        {"name": "NCMH Crisis Hotline", "number": "1553", "hours": "24/7, free from landline and mobile"},
    ],
}


def check_answers(instrument: str, answers: list[int]) -> None:
    spec = INSTRUMENTS[instrument]
    if len(answers) != spec.items:
        raise ValueError(f"{spec.name} needs exactly {spec.items} answer(s), got {len(answers)}")
    for position, answer in enumerate(answers, start=1):
        if not spec.low <= answer <= spec.high:
            raise ValueError(f"{spec.name} answer {position} must be from {spec.low} to {spec.high}")


def total_score(answers: list[int]) -> int:
    return sum(answers)


def band(instrument: str, score: int) -> str:
    for upper, label in INSTRUMENTS[instrument].bands:
        if score <= upper:
            return label
    raise ValueError("score out of range")

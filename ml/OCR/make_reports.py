"""Makes 3 synthetic lab report images with invented values (no real data) for the OCR check:
clean.png (screenshot-like), photo.jpg (tilted, shadowed phone photo), handwritten.jpg
(three values written by hand). Writes truth.json with the values the app should store.

    uv run python ml/OCR/make_reports.py            # -> ml/OCR/output/ (not committed)

Fixed seed: the same images every time. Fonts are Fedora's DejaVu and Z003; elsewhere
Pillow's own font is used (the values are the same, the look differs)."""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "output"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(7)

SANS = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"
HAND = "/usr/share/fonts/urw-base35/Z003-MediumItalic.otf"

# label, value as printed, unit, reference range, flag
ROWS = [
    ("Fasting Blood Sugar (FBS)", "5.3", "mmol/L", "3.9 - 5.5", ""),
    ("HbA1c", "5.9", "%", "4.0 - 5.6", "H"),
    ("Total Cholesterol", "212", "mg/dL", "< 200", "H"),
    ("HDL Cholesterol", "1.12", "mmol/L", "> 1.04", ""),
    ("LDL Cholesterol", "138", "mg/dL", "< 130", "H"),
    ("Triglycerides", "1.65", "mmol/L", "< 1.70", ""),
    ("Creatinine", "88", "umol/L", "62 - 106", ""),
    ("Uric Acid", "6.1", "mg/dL", "3.5 - 7.2", ""),
    ("TSH", "2.15", "uIU/mL", "0.34 - 4.25", ""),
]
HANDWRITTEN = {"HDL Cholesterol", "Creatinine", "TSH"}  # values written in by hand on image 3

# What the parser should give (standard units, 3 decimals), from backend/labs.py factors
TRUTH = {"FPG": "95.485", "HBA1C": "5.900", "TOTAL_CHOLESTEROL": "212.000", "HDL": "43.310",
         "LDL": "138.000", "TRIGLYCERIDES": "146.141", "CREATININE": "0.995", "URIC_ACID": "6.100",
         "TSH": "2.150"}


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:  # not on Fedora
        return ImageFont.load_default(size)


def report(handwritten=False):
    width, height = 1240, 1180
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    draw.text((60, 50), "SAMPLE DIAGNOSTIC LABORATORY", font=font(BOLD, 40), fill="black")
    draw.text((60, 105), "123 Invented St., Sample City  |  Tel. 000-0000", font=font(SANS, 22), fill="#333")
    draw.line((60, 145, width - 60, 145), fill="black", width=3)
    info = font(SANS, 24)
    draw.text((60, 165), "Patient: SAMPLE, TEST A.      Age/Sex: 45 / F", font=info, fill="black")
    draw.text((60, 200), "Date collected: 2026-01-15    Specimen: Serum (fasting)", font=info, fill="black")
    draw.text((60, 250), "CLINICAL CHEMISTRY", font=font(BOLD, 28), fill="black")

    columns = [60, 520, 700, 860, 1100]
    top, row_h = 300, 62
    head = font(BOLD, 24)
    for x, title in zip(columns, ["Test", "Result", "Unit", "Reference", "Flag"]):
        draw.text((x + 10, top + 16), title, font=head, fill="black")
    cell = font(SANS, 25)
    for i, (label, value, unit, ref, flag) in enumerate(ROWS, start=1):
        y = top + i * row_h
        draw.text((columns[0] + 10, y + 16), label, font=cell, fill="black")
        if handwritten and label in HANDWRITTEN:
            hand_write(page, value, (columns[1] + 18, y + 6))
        else:
            draw.text((columns[1] + 10, y + 16), value, font=cell, fill="black")
        draw.text((columns[2] + 10, y + 16), unit, font=cell, fill="black")
        draw.text((columns[3] + 10, y + 16), ref, font=cell, fill="black")
        draw.text((columns[4] + 10, y + 16), flag, font=font(BOLD, 25), fill="black")
    bottom = top + (len(ROWS) + 1) * row_h
    for i in range(len(ROWS) + 2):
        draw.line((columns[0], top + i * row_h, width - 60, top + i * row_h), fill="#555", width=2)
    for x in columns + [width - 60]:
        draw.line((x, top, x, bottom), fill="#555", width=2)
    draw.text((60, bottom + 40), "Results reviewed by: (invented) Medical Technologist, RMT", font=info, fill="black")
    draw.text((60, bottom + 80), "This is a synthetic test document. Not a real patient.", font=info, fill="#555")
    return page


def hand_write(page, text, origin):
    """Ballpoint-blue digits, each with its own size, tilt and baseline."""
    x, y = origin
    for ch in text:
        size = random.randint(40, 48)
        glyph = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(glyph).text((size // 2, size // 4), ch, font=font(HAND, size), fill=255)
        glyph = glyph.rotate(random.uniform(-12, 8), resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.6))
        ink = Image.new("RGB", glyph.size, (25, 45, 140))
        page.paste(ink, (x - size // 2, y + random.randint(-4, 4) - size // 4), glyph)
        x += int(size * (0.42 if ch == "." else 0.62)) + random.randint(-2, 3)


def phone_photo(page):
    """Tilted, slightly out of focus, uneven light, on a desk, JPEG."""
    w, h = page.size
    desk = Image.new("RGB", (w + 260, h + 260), (120, 96, 70))
    tilted = page.rotate(-3.5, resample=Image.BICUBIC, expand=True, fillcolor=(120, 96, 70))
    desk.paste(tilted, (110, 90))
    shade = Image.linear_gradient("L").resize(desk.size).rotate(35)
    dark = Image.new("RGB", desk.size, (30, 30, 40))
    photo = Image.composite(dark, desk, shade.point(lambda v: int(v * 0.45)))
    photo = photo.filter(ImageFilter.GaussianBlur(1.1))
    noise = Image.effect_noise(photo.size, 18).convert("RGB")
    photo = Image.blend(photo, noise, 0.06)
    return photo.resize((int(photo.width * 1.6), int(photo.height * 1.6)), Image.BICUBIC)


report().save(OUT / "clean.png")
phone_photo(report()).save(OUT / "photo.jpg", quality=78)
phone_photo(report(handwritten=True)).save(OUT / "handwritten.jpg", quality=80)
(OUT / "truth.json").write_text(json.dumps(TRUTH, indent=2))
print("written to", OUT)

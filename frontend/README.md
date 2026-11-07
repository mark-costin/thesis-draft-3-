# Patient Portal (Reflex + Radix UI)

The patient screens, written in Python with Reflex and Radix UI components (designed by the UI team, Thesis_UI.zip).
Screens 1 to 4 (log in, create account, forgot and reset password, account settings, dashboard, vitals and lifestyle,
family history) are connected to the CDSS API. Scan & assess, Results and Reports still show sample data until
Sprint 5; their `TODO (Sprint 5)` comments mark where the API plugs in. Export and delete account are connected.

## Run (from the repo root)

Two programs run side by side: the API (port 8000) and this app (3000 in the browser, 8001 for Reflex's own backend).

```bash
uv sync                                                   # Reflex is in the project's dependencies
uv run uvicorn backend.main:app --reload                  # terminal 1: the API on http://localhost:8000
cd frontend && uv run reflex run                          # terminal 2: open http://localhost:3000
```

The first `reflex run` downloads its JavaScript tools, so it needs internet once.
Settings come from the root `.env` (only these two are read by the app; see `patient_portal/api.py`):

| Setting | Default | Meaning |
|---|---|---|
| `CDSS_API_URL` | `http://localhost:8000` | Where the API runs |
| `GOOGLE_CLIENT_ID` | empty (Google buttons disabled) | The same client id the API checks Google tokens against |

`CORS_ORIGINS` in `.env` can stay `["http://localhost:3000"]`: the browser never calls the API directly,
Reflex does it from Python. The login token is kept in a backend-only State variable (`_token`), so it never
reaches the browser.

## Screens

| Route | File | Radix components used |
|---|---|---|
| `/` | `pages/login.py` | Form, Input, Button (Facebook-style log in) |
| `/register` | `pages/register.py` | Form, Input, Select (Province > City > Barangay), Checkbox, Button |
| `/forgot-password` | `pages/forgot.py` | Form, Input, Button |
| `/account` | `pages/account.py` | Form, Input, Select, Card (personal details; security & authentication) |
| `/dashboard` | `pages/dashboard.py` | Card, Badge, Progress, Callout, Recharts line charts |
| `/vitals` | `pages/vitals.py` | Tabs, Form, Input, Select, Radio Group, Table |
| `/family` | `pages/family.py` | Form (exact relative, condition, age, confirmed, same household), Table, Badge (FHRS band per disease) |
| `/scan` | `pages/scan.py` | Upload, two-column Grid, Input, Badge, Callout, Button |
| `/results` | `pages/results.py` | Progress, Card, Recharts bar chart (SHAP), Badge, Callout (care plan from `careplan.py`) |
| `/reports` | `pages/reports.py` | Table, Checkbox, Button, Alert Dialog |

Pages other than `/`, `/register`, `/forgot-password` and `/reset-password` redirect to the login screen until `State.is_logged_in` is true. `/reset-password?token=...` is the page the reset email links to.
Colors and fonts: `assets/theme.css` and the theme plugin in `rxconfig.py`.
Look: clinic palette (one teal-blue brand colour on clean white and cool blue-grey neutrals), Figtree font. The name and avatar in the top bar open a menu: Profile & account, Theme, Log out. Green, amber and red appear only to show status (risk tier, in-range, warnings).
Toast messages appear in the top-right corner (`toaster=` in `patient_portal/patient_portal.py`). See `CHANGES.md` for the full list of changes.

## Address drop-downs

Province, City or municipality and Barangay are drop-downs sorted A to Z. On first start the app downloads the PSGC list once and saves it to `data/ph_locations.json` (internet needed that one time; or run `python tools/build_locations.py`). Offline, a small starter list is used.

## Care plan (from `careplan_2026-10-27.docx`)

`patient_portal/careplan.py` loads the 12 templates (4 diseases x 3 tiers) from `patient_portal/data/careplan_templates.json`.

- **Tier by probability:** Low below 0.20, Moderate 0.20 to below 0.50, High 0.50 and above. The tier picks the template (for example `T2D-MODERATE`).
- **Add-ons** show only when their trigger is true in the patient's saved data. The rules are in `TRIGGERS` (smoking, dust exposure, family history band 3 or 4, sleep hours, sodium, and so on). The app fails at start-up if a template has an add-on with no rule.
- **Goals:** "Add as goal" appears only on items marked Goal = Yes. Urgent warning signs and the NCMH Crisis Hotline (1553) item are shown in a red box with no goal button.
- **Every plan** shows the Basis (Sourced, System rule, Plan default) and Source for each item, the template id and version, the reviewer line, and the educational-screening disclaimer.
- **Physician sign-off:** a plan shows "Pending physician review" until `REVIEW` in `careplan.py` has an entry for that template (`{"CVD-LOW": {"by": "Dr. Name", "on": "2026-11-05"}}`). Any change after approval should be a new version, not an edit.
- **Saved reports** keep the template ids and version used.
- To rebuild the JSON from a revised document: `python tools/parse_careplan.py careplan.docx patient_portal/data/careplan_templates.json`.

Open items for the physician (document, section 6): the BMI trigger for T2D add-on A6 is 25 in `BMI_TRIGGER` (23 if ADA's Asian cutoff is chosen). Family history bands (1 to 4) use a placeholder scoring rule in `family_band()` until the CDSS Data Dictionary rule is confirmed. Fields the care plan needs but the screens did not have (alcohol use, sitting time, sodium, PSQI, TSH, B12, allergic rhinitis, acanthosis nigricans) were added to the Lifestyle and Health history tabs.

## Google account

Sign-in and sign-up with Google use the `reflex-google-auth` button (Google Identity Services, ID-token flow).
The ID token is sent to the API (`POST /auth/google`), which checks it with Google; this app never decides who
someone is. A new Google user is sent to Create account to add the date of birth, biological sex and consent
(Google does not share them), then presses Sign up with Google again. A Google email that already has a password
account is not linked: the API answers 409 and the patient logs in with the password.

1. In Google Cloud Console, create an OAuth client of type **Web application**.
2. Under Authorized JavaScript origins add `http://localhost:3000` and your production domain.
3. Put the client id in the root `.env` as `GOOGLE_CLIENT_ID=<id>.apps.googleusercontent.com` (the API and this app both read it).

## Changes made when connecting to the API

- **Biological sex** instead of "Gender" on Create account and Account settings: the models and the FHRS use sex assigned at birth.
- **Family history** is one entry per relative and diagnosis (exact relative, condition, age at diagnosis, confirmed, same household),
  because the FHRS needs the relative's degree and sex and the onset age. The score is computed by the API, not here.
- **Lifestyle** fields match the API: activity in minutes a week, dust at work yes or no, substance use none / past / current,
  carbohydrates instead of the diet drop-down. TSH and vitamin B12 are saved as typed-in lab values. The PSQI score moves to the questionnaire screen (Sprint 5).
- **Vitals:** a blood sugar reading needs its context (fasting, random, 2 hours after a meal). Weight, height and waist are sent only when changed.
- **Health history** has a Save button (PUT /health-history); gestational diabetes is asked of female patients only.
- **Dashboard:** the missing-information list comes from `GET /assessment/readiness`; risk cards show the latest assessment, or "No assessment yet".
- **Delete account** asks for the password (or a fresh Google sign-in) and the phrase DELETE MY ACCOUNT, as the API requires.
- New page `/reset-password` for the emailed link.
- **Double click / lost connection:** the same save sent again within 10 seconds reuses its Idempotency-Key
  (`api.REPEAT_WINDOW`), so the API saves one record and the screen still gets the saved record back.

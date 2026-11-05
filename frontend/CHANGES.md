# What changed (round 6: start-up fix)

- **Fixed:** `AttributeError: No reflex attribute set_color_mode`. The Theme choices in the user menu used a Reflex function that newer Reflex versions do not have at the top level. The app now looks for it in each place Reflex has kept it, so it starts on your version. File: `ui.py` (`set_mode`, `toggle_mode`).
- If one Theme choice does nothing when clicked, tell me the Reflex version (`pip show reflex`) and I will match it exactly.

---

# What changed (round 5: form checks and password section)

- **Names (First, Last):** numbers and symbols cannot be typed. Letters, spaces, dots, hyphens and apostrophes are allowed (so "Dela Cruz-Reyes Jr." and "O'Brien" work). Checked again on the server.
- **Contact number:** letters cannot be typed. Only digits (and a leading +), 13 characters at most. Checked again on the server: `09171234567` or `+639171234567`.
- **Gender:** "Biological sex" is now **Gender** on Create account, Account settings and Scan & assess. The choices are still Female and Male. If the risk model needs biological sex, tell me and we can keep that wording in the clinical screens.
- **Password section:** the long hint text is gone and the strength bar is one slim line with its word (Weak, Fair, Good, Strong) beside it, so the section is shorter. The placeholder text on Confirm password is gone too.
- **Passwords match message:** under Confirm password it says "✓ Passwords match" in green or "✕ Passwords do not match" in red as you type. It also works in Account settings (New password / Confirm password). The comparison happens in the browser, so the password is never sent to the server just to check it.
- Files: `auth_ui.py`, `pages/register.py`, `pages/account.py`, `pages/scan.py`, `state.py`, `assets/theme.css`.

---

# What changed (round 4: user menu and clinic palette)

## Top bar
- The name and avatar are now a **drop-down menu**: **Profile & account** (opens Account settings), **Theme** (Light, Dark, Match my device) and **Log out**.
- The separate theme button and Log out button are gone from the top bar (they live in the menu).
- "Patient Portal / Private and self-hosted" on the left is **removed**. Only the small logo mark stays, so people can still get back to the dashboard.
- Files: `ui.py` (`user_menu`, `brand`), `assets/theme.css`.

## Clinic colour palette, easier for everyone to use
- Warm beige neutrals are replaced by **clean clinic white and cool pale blue-grey**; the brand colour is a calm **teal-blue** (`#0B7285`). Green, amber and red still appear only for status.
- Text contrast is higher: body text 15.7 : 1, helper text 7.3 : 1 on white (AA needs 4.5 : 1). Dark mode retuned to match.
- Text and controls are about 5% larger (theme scaling 110%) and inputs and selects are 44 px tall, which is easier to read and tap.
- Files: `assets/theme.css`, `rxconfig.py`, `pages/dashboard.py`, `pages/results.py` (chart line colours).

## Not run on my side
- Reflex could not be installed here, so run `reflex run` and open the menu to check it.

---

# What changed (round 3: sign-in, create account, account settings)

## Log in (`/`) - Facebook style
- One centred card: Email, Password (with show/hide), **Log in**, "Forgot password?", "or", Continue with Google, then **Create new account**.
- No "Welcome back" heading and no sub-text. (A hidden "Log in" heading stays for screen readers.)
- The teal "Know your risk early..." panel (Your health in one place, Private by design, Screening not diagnosis, Clear next steps) is **removed**.
- Files: `pages/login.py`, `assets/theme.css` (the `.pp-hero` style is gone).

## Create account (`/register`, its own page)
- Personal details: First name, Last name, Date of birth, Biological sex, **Email address**, **Contact number** (Philippine mobile: `09171234567` or `+639171234567`).
- **Password + Confirm password** (the two must match; 12 characters minimum; strength meter kept).
- **Home address:** Province, City or municipality and Barangay are drop-downs that depend on each other (changing the province clears the city and barangay). House no. / street is an optional text box.
- Privacy Notice (RA 10173) box and Google sign-up kept.
- Files: `pages/register.py`, `auth_ui.py` (shared pieces), `state.py` (`register`, `set_province`, `set_city`, `set_barangay`, ...).

## Account settings (`/account`)
- **Personal details:** every detail above in one place (name, date of birth, sex, email, contact number, home address), with Save changes.
- **Security & authentication:** Current password, New password, Confirm password, Change password.
- **Forgot password** here and on the log in page uses email: it sends a single-use reset link (valid one hour). The reply is the same whether or not the address has an account, so nobody can use it to find out who is registered. Page: `/forgot-password`.
- Google sign-in accounts see a note instead of the password form (their password is managed at Google) and keep the email Google verified.
- Open it by clicking your name or avatar in the top bar (also in the mobile menu).
- Files: `pages/account.py`, `pages/forgot.py`, `ui.py`, `patient_portal.py`, `state.py` (`save_profile`, `change_password`, `send_reset_email`).

## Address lists - all drop-downs, sorted A to Z
- Province, City or municipality and Barangay are all drop-downs, each sorted A to Z (numbers in order, so Barangay 2 comes before Barangay 10). Example: pick Bulacan and the cities run Angat to Santa Maria; pick Marilao and the barangays run Abangan Norte to Tabing Ilog.
- On first start the app downloads the official PSGC list (every city and barangay) once and saves it to `data/ph_locations.json`. It needs internet that one time. You can also run `python tools/build_locations.py` yourself.
- If the download fails, a short starter list is used (all provinces, Metro Manila cities, Bulacan cities, Marilao barangays) and the form shows a note. The starter list was typed from memory, so the downloaded list replaces it.
- Files: `locations.py`, `auth_ui.py`, `tools/build_locations.py`.

## Not changed / still sample behaviour
- Register, log in, save details, change password and the reset email only show the right messages. The `TODO` comments in `state.py` mark where the FastAPI calls go. Passwords are never kept in `State`.
- This round was not run on my side (Reflex could not be installed here). Run `reflex run` and click through `/`, `/register`, `/account` and `/forgot-password` first.

---

# Earlier changes (rounds 1 and 2)

## Design (humanized, modern, simple)
- **One brand colour.** Cyan, indigo, orange, gradients, glows and the dark hero panel are gone. The app now uses teal on warm off-white neutrals. Green / amber / red remain only for status (risk tier, "in range", warnings).
- **Risk tiers:** Low = green, Moderate = amber, High = red (High was orange, too close to amber). Every tier still shows its name in text.
- **Softer look:** flat cards with a hairline border, round icon tiles, pill-shaped nav, no hover "lift", no coloured top edge on risk cards, no coloured glow on buttons.
- **Friendlier type and wording:** Figtree font; sentence-case labels instead of ALL CAPS (table headers, stat labels, page eyebrows); "Create my account", "Log in", "Save vitals", "Download PDF", "Start assessment", and similar.
- **Sign-in page:** the dark gradient panel is now a soft teal-tint panel with readable dark text.
- **Care plan page:** the disclaimer is a neutral grey note (it was an amber warning, which made every page look like an alert). Labels use grey/teal instead of cyan/indigo.
- Dark mode retuned to the same palette.
- Files: `assets/theme.css` (rewritten), `rxconfig.py`, `ui.py`, and every file in `pages/`.

## Toast notifications
- Now at the **top right**, below the top bar, with an X to close, max 3 visible, 4 second timeout, themed to match (light and dark). Full width on phones.
- Files: `patient_portal.py` (`toaster=`), `theme.css` section 4.

## Fixes and improvements
1. **Logout now clears the session data.** Before, the next person to log in on the same browser session could see the previous person's records. (`state.py`, `logout`). It also shows a "logged out" toast.
2. **No flash of private data when logged out.** Signed-in pages used to render for a moment before redirecting to sign in. They now show a short "Taking you to sign in" screen instead. (`ui.py`, `shell`)
3. **Signed-in users skip the sign-in page.** Opening `/` while logged in goes straight to the dashboard. (`state.py`, `redirect_if_logged_in`)
4. **Vitals form checks its input.** Blood pressure is required, and fields have realistic min/max (for example, SpO2 50 to 100). Before, you could save a row with a blank "/" blood pressure. (`pages/vitals.py`, `state.save_vitals`)
5. **Disabled "Run assessment" button now explains why.** A line under it lists what is still missing, for example "Still needed: smoking status, waist circumference." (`state.run_hint`, `pages/scan.py`)
6. **Top bar no longer overflows at laptop widths** (the Log out button was cut off at about 1360 px). Name and ID show only on very wide screens; the avatar always shows. (`ui.py`)
7. **Icons in the Vitals page tabs** had no gap before the text. Fixed.
8. **SHAP chart label** "Blood pressure" was clipped. Axis widened.
9. **Password strength meter** uses red / amber / green only.

## Not changed (worth knowing)
- Login and register still accept any input. That is sample behaviour; the `TODO` comments in `state.py` mark where the FastAPI auth call goes. Do not ship it before that is connected.
- `Open` on a saved report and the PDF download still show a placeholder toast.

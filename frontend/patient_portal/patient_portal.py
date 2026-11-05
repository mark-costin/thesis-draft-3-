import reflex as rx

from .pages import account, dashboard, family, forgot, login, questionnaires, register, reports, reset, results, scan, vitals
from .state import State

app = rx.App(
    stylesheets=["/theme.css", "https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&display=swap"],
    # Toasts appear in the top-right corner, just below the sticky top bar, and can be dismissed with the X.
    toaster=rx.toast.provider(position="top-right", close_button=True, offset="80px", visible_toasts=3, duration=4000),
)
app.add_page(login.page, route="/", title="Log in | Patient Portal", on_load=State.redirect_if_logged_in)
app.add_page(register.page, route="/register", title="Create account | Patient Portal", on_load=State.redirect_if_logged_in)
app.add_page(forgot.page, route="/forgot-password", title="Reset password | Patient Portal")
app.add_page(reset.page, route="/reset-password", title="New password | Patient Portal")  # the emailed link (backend/mailer.py)
# Each signed-in page loads its data from the API when it opens (and sends the visitor to log in if needed)
for route, mod, name, load in [("/dashboard", dashboard, "Dashboard", State.load_dashboard), ("/vitals", vitals, "Vitals & lifestyle", State.load_vitals_page),
                               ("/questionnaires", questionnaires, "Questionnaires", State.load_questionnaires_page),
                               ("/family", family, "Family history", State.load_family_page), ("/scan", scan, "Scan & assess", State.load_dashboard),
                               ("/results", results, "Results", State.check_login), ("/reports", reports, "Reports", State.check_login),
                               ("/account", account, "Account settings", State.check_login)]:
    app.add_page(mod.page, route=route, title=f"{name} | Patient Portal", on_load=load)

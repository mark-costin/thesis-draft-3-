import os

import reflex as rx

# Ports: the CDSS API (FastAPI, backend/) runs on 8000, so Reflex's own backend uses 8001.
# The browser talks only to Reflex (3000 and 8001); Reflex calls the API from Python (api.py).
config = rx.Config(
    app_name="patient_portal",
    frontend_port=3000,
    backend_port=8001,
    # Where the browser reaches Reflex's backend (WebSocket /_event, uploads). On the server this is
    # the public HTTPS address behind Nginx (REFLEX_API_URL, set by docker/Dockerfile.web).
    api_url=os.environ.get("REFLEX_API_URL", "http://localhost:8001"),
    plugins=[
        rx.plugins.RadixThemesPlugin(theme=rx.theme(accent_color="teal", gray_color="slate", radius="large", scaling="110%", appearance="inherit")),
        rx.plugins.SitemapPlugin(),
    ],
)

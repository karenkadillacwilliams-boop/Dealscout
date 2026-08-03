"""Mirror Streamlit Cloud's st.secrets into os.environ at startup.

The catalysts/, alerts/, and portfolio modules read configuration via
os.environ.get(...) so they work identically under cron, pytest, and Windows
Task Scheduler. On Streamlit Community Cloud there is no .env file — secrets
live in st.secrets — so we copy them across once before those modules are
imported.

Locally st.secrets is empty and this is a no-op; app.py calls load_dotenv()
immediately before this to populate os.environ from .env. Because we use
setdefault, .env always wins over st.secrets when both are present.
"""
from __future__ import annotations

import os

import streamlit as st


def hydrate_env_from_secrets() -> None:
    try:
        items = list(st.secrets.items())
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return  # local dev: no secrets.toml, .env already loaded

    for key, value in items:
        if isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(key, str(value))

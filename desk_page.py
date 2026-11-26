"""The hosted site's Streamlit page (ADR 0068): the Dispute Desk, filling the window.

The desk is served beside Streamlit at desk/ (streamlit_app.py). This page only frames it, so
the site opens on the new workspace. The address is relative: the host serves everything under
a /~/+/ prefix.
"""

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Dispute Desk",
    page_icon=":material/balance:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.html(
    "<style>"
    "header[data-testid='stHeader'], [data-testid='stToolbar'], footer { display: none !important; }"
    ".stApp, [data-testid='stAppViewContainer'], [data-testid='stMain'] { overflow: hidden; }"
    ".block-container, [data-testid='stMainBlockContainer'] {"
    " padding: 0 !important; max-width: none !important; }"
    "[data-testid='stVerticalBlock'] { gap: 0 !important; }"
    "[data-testid='stElementContainer']:has(iframe) iframe {"
    " width: 100vw !important; height: 100vh !important; border: 0; display: block; }"
    "</style>"
)
components.iframe("desk/", height=900, scrolling=False)

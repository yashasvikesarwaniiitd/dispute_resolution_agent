"""The console's stylesheet reaches the page (it is injected through st.html)."""

from __future__ import annotations

import re

from dispute_agent.ui.theme import CSS_FILE, stylesheet


def test_no_tag_like_text_inside_the_style_element() -> None:
    # Streamlit's sanitiser drops a whole style element whose text holds a tag such as "<link>",
    # even in a comment: the console then rendered with no styling at all (2026-11-27).
    css = CSS_FILE.read_text(encoding="utf-8")
    assert not re.search(r"<[/!a-zA-Z]", css)
    assert stylesheet().count("<") == 2  # only the style element's own open and close tags

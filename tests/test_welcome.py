"""Welcome page: it describes every menu page (and nothing else), it is the landing page, and its buttons navigate.

The drift test reads the menu from app.py's source, so it runs in CI without Streamlit.
"""
import ast
import re
from pathlib import Path

import pytest

from reports import welcome

APP = Path(__file__).resolve().parent.parent / "app.py"


def _menu_items() -> list[str]:
    block = re.search(r"MENU_ITEMS = (\[.*?\])", APP.read_text(encoding="utf-8"), re.S).group(1)
    tree = ast.parse(block.replace("WELCOME", repr(welcome.WELCOME)), mode="eval")
    return ast.literal_eval(tree.body)


def test_every_menu_page_is_described_once():
    menu = _menu_items()
    assert menu[0] == welcome.WELCOME
    described = welcome.menu_pages()
    assert sorted(set(described)) == sorted(described), "a page is listed twice"
    assert set(described) == set(menu) - {welcome.WELCOME}
    assert {page for page, *_ in welcome.QUICK_START} <= set(menu)


def test_welcome_is_the_landing_page_and_buttons_navigate():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    assert at.title[0].value.startswith("👋 Welcome")
    assert any("Fetch All Jira Tickets" in i.value for i in at.info)          # nothing loaded yet
    at.button(key="welcome_quick_🛡️  SLA (Service Level Agreements)").click().run()
    assert at.title[0].value.startswith("🛡️ SLA")

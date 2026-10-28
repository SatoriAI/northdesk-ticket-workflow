from northdesk.agent.tools import TOOL_SPECS
from tests.support import REPO, toolbox_for


def test_search_kb__finds_the_password_article() -> None:
    results = toolbox_for(REPO.tickets["T-1"]).search_kb("forgot password")

    assert results[0]["article_id"] == "KB-1"


def test_search_kb__plural_and_case_do_not_matter() -> None:
    results = toolbox_for(REPO.tickets["T-4"]).search_kb("Suspended ACCOUNT")

    assert results[0]["article_id"] == "KB-4"


def test_search_kb__no_match_is_empty() -> None:
    assert toolbox_for(REPO.tickets["T-5"]).search_kb("pizza") == []


def test_get_invoices__amounts_are_strings() -> None:
    invoices = toolbox_for(REPO.tickets["T-2"]).get_invoices()

    assert invoices[0]["amount_eur"] == "49.0"


def test_tool_specs__are_read_only_plus_submit() -> None:
    assert [spec["name"] for spec in TOOL_SPECS] == ["get_customer", "get_invoices", "search_kb", "submit_decision"]

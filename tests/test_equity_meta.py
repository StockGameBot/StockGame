"""Tests for company-name lookup and autocomplete labels."""

from helpers.equity_meta import autocomplete_label, lookup_company_name


def test_autocomplete_label_omits_empty_or_ticker_name():
    assert autocomplete_label("RACE", None) == "RACE"
    assert autocomplete_label("RACE", "") == "RACE"
    assert autocomplete_label("RACE", "RACE") == "RACE"
    assert autocomplete_label("RACE", "  race  ") == "RACE"
    assert autocomplete_label("RACE", "Ferrari N.V.") == "RACE - Ferrari N.V."


def test_lookup_company_name_uses_yahoo_when_alpaca_fails(mocker):
    fake_alpaca = mocker.Mock()
    fake_alpaca.configured = True
    fake_alpaca.get_us_equity.side_effect = RuntimeError("401")

    mocker.patch(
        "helpers.equity_meta.requests.get",
        return_value=mocker.Mock(
            raise_for_status=lambda: None,
            json=lambda: {
                "quotes": [
                    {
                        "symbol": "RACE",
                        "shortname": "Ferrari N.V.",
                        "longname": "Ferrari N.V.",
                    }
                ]
            },
        ),
    )
    assert lookup_company_name("RACE", alpaca=fake_alpaca) == "Ferrari N.V."


def test_lookup_company_name_prefers_alpaca_when_available(mocker):
    fake_alpaca = mocker.Mock()
    fake_alpaca.configured = True
    fake_alpaca.get_us_equity.return_value = {"name": "Apple Inc. Common Stock"}
    yahoo = mocker.patch("helpers.equity_meta.requests.get")
    assert lookup_company_name("AAPL", alpaca=fake_alpaca) == "Apple Inc. Common Stock"
    yahoo.assert_not_called()

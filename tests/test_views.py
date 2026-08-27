from types import SimpleNamespace

from helpers.views import StockPortfolioImageGenerator, create_portfolio_image


def test_portfolio_image_convenience_wrapper_returns_png():
    info = SimpleNamespace(game=SimpleNamespace(start_money=10_000, pick_count=10))
    image = create_portfolio_image(
        user_data={"display_name": "Investor", "user_id": 1},
        game_data={"name": "Example", "id": "ABCDE"},
        stock_picks=[],
        info=info,
    )

    assert image.read(8) == b"\x89PNG\r\n\x1a\n"


def test_portfolio_company_name_skips_ticker_placeholder():
    generator = StockPortfolioImageGenerator()
    assert generator._portfolio_company_name(
        {"stock_ticker": "AAPL", "company_name": "AAPL"}
    ) is None
    assert generator._portfolio_company_name(
        {"stock_ticker": "AAPL", "company_name": "Apple Inc."}
    ) == "Apple Inc."


def test_stock_display_name_formats_ticker_and_company():
    generator = StockPortfolioImageGenerator()
    assert generator._stock_display_name(
        {"stock_ticker": "AAPL", "company_name": "Apple Inc."}
    ) == "AAPL | Apple Inc."
    assert generator._stock_display_name(
        {"stock_ticker": "AAPL", "company_name": "AAPL"},
        pending=True,
    ) == "AAPL*"

def test_portfolio_money_left_uses_unfilled_pick_slots():
    """Money left is slot-based cash, not starting money minus market value."""
    start_money = 10_000
    pick_count = 10
    value_per_pick = start_money / pick_count

    # Fully invested - $0 left even when stocks are up or down
    assert start_money - 10 * value_per_pick == 0

    # One empty slot
    assert start_money - 9 * value_per_pick == value_per_pick

    # Eight owned + one pending - one slot still open
    assert start_money - (8 + 1) * value_per_pick == value_per_pick


def test_portfolio_image_renders_with_company_names():
    info = SimpleNamespace(game=SimpleNamespace(start_money=10_000, pick_count=10))
    image = create_portfolio_image(
        user_data={"display_name": "Investor", "user_id": 1},
        game_data={"name": "Example", "id": "ABCDE"},
        stock_picks=[
            {
                "stock_ticker": "AAPL",
                "company_name": "Apple Inc.",
                "status": "owned",
                "shares": 10.0,
                "current_value": 1100.0,
                "change_dollars": 100.0,
                "change_percent": 10.0,
            },
            {
                "stock_ticker": "MSFT",
                "company_name": "Microsoft Corporation",
                "status": "pending_buy",
            },
        ],
        info=info,
    )

    assert image.read(8) == b"\x89PNG\r\n\x1a\n"

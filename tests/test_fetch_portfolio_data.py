from unittest.mock import MagicMock


def _mock_ticker(price, prev_close, currency="USD", pe=25.0):
    t = MagicMock()
    t.fast_info = MagicMock()
    t.fast_info.last_price = price
    t.fast_info.previous_close = prev_close
    t.fast_info.currency = currency
    t.info = {
        "trailingPE": pe,
        "marketCap": 1_000_000_000,
        "fiftyTwoWeekHigh": price * 1.5,
        "fiftyTwoWeekLow": price * 0.5,
    }
    return t


def test_pence_converted_to_gbp():
    from tools.fetch_portfolio_data import build_row
    # LSE lines are quoted in pence (GBp); 9550p -> £95.50
    ticker_obj = _mock_ticker(price=9550, prev_close=9400, currency="GBp")
    row = build_row("VWRP", "VWRP.L", ticker_obj, usd_gbp_rate=1.27, eur_gbp_rate=0.85)
    assert abs(row["price_gbp"] - 95.50) < 0.01, f"Expected ~95.50, got {row['price_gbp']}"


def test_usd_ticker_unchanged():
    from tools.fetch_portfolio_data import build_row
    ticker_obj = _mock_ticker(price=156.50, prev_close=150.0, currency="USD")
    row = build_row("PLTR", "PLTR", ticker_obj, usd_gbp_rate=1.27, eur_gbp_rate=0.85)
    assert row["price_usd"] == 156.50
    assert abs(row["price_gbp"] - 156.50 / 1.27) < 0.01


def test_eur_ticker_converted():
    from tools.fetch_portfolio_data import build_row
    ticker_obj = _mock_ticker(price=100.0, prev_close=98.0, currency="EUR")
    row = build_row("SAMPLE", "SAMPLE.DE", ticker_obj, usd_gbp_rate=1.27, eur_gbp_rate=0.85)
    assert abs(row["price_gbp"] - 85.0) < 0.01  # 100 EUR * 0.85


def test_day_change_calculated():
    from tools.fetch_portfolio_data import build_row
    ticker_obj = _mock_ticker(price=110.0, prev_close=100.0, currency="USD")
    row = build_row("NVDA", "NVDA", ticker_obj, usd_gbp_rate=1.27, eur_gbp_rate=0.85)
    assert abs(row["day_change_pct"] - 10.0) < 0.01

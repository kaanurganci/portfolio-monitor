def test_sector_weights_sum_to_100():
    from tools.portfolio_analytics import compute_sector_weights
    holdings = [
        {"ticker": "NVDA", "price_gbp": 100.0, "shares": 5, "sector": "Semiconductors"},
        {"ticker": "AMD", "price_gbp": 100.0, "shares": 5, "sector": "Semiconductors"},
        {"ticker": "AAPL", "price_gbp": 200.0, "shares": 2, "sector": "Big Tech"},
    ]
    weights = compute_sector_weights(holdings)
    total = sum(w["weight_pct"] for w in weights)
    assert abs(total - 100.0) < 0.01


def test_concentration_flag_above_threshold():
    from tools.portfolio_analytics import compute_sector_weights
    holdings = [
        {"ticker": "NVDA", "price_gbp": 100.0, "shares": 10, "sector": "Semiconductors"},
        {"ticker": "AMD", "price_gbp": 100.0, "shares": 10, "sector": "Semiconductors"},
        {"ticker": "AAPL", "price_gbp": 10.0, "shares": 1, "sector": "Big Tech"},
    ]
    weights = compute_sector_weights(holdings)
    semis = next(w for w in weights if w["sector"] == "Semiconductors")
    assert semis["concentration_flag"] is True  # > 30%


def test_no_flag_when_balanced():
    from tools.portfolio_analytics import compute_sector_weights
    holdings = [
        {"ticker": "A", "price_gbp": 100.0, "shares": 1, "sector": "Tech"},
        {"ticker": "B", "price_gbp": 100.0, "shares": 1, "sector": "Finance"},
        {"ticker": "C", "price_gbp": 100.0, "shares": 1, "sector": "Energy"},
        {"ticker": "D", "price_gbp": 100.0, "shares": 1, "sector": "Health"},
    ]
    weights = compute_sector_weights(holdings)
    assert all(not w["concentration_flag"] for w in weights)

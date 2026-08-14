from unittest.mock import patch, MagicMock
from datetime import date, timedelta
import pandas as pd


def _make_earnings_df(days_from_now):
    future_date = date.today() + timedelta(days=days_from_now)
    return pd.DataFrame({"Earnings Date": [pd.Timestamp(future_date)]})


def test_upcoming_earnings_returned():
    from tools.check_earnings_calendar import get_upcoming_earnings
    with patch("tools.check_earnings_calendar.yf.Ticker") as mock_ticker_cls:
        mock_t = MagicMock()
        mock_t.calendar = _make_earnings_df(5)
        mock_ticker_cls.return_value = mock_t
        results = get_upcoming_earnings(["NVDA"], days_ahead=30)
        assert len(results) == 1
        assert results[0]["ticker"] == "NVDA"
        assert results[0]["days_until"] == 5


def test_earnings_beyond_window_excluded():
    from tools.check_earnings_calendar import get_upcoming_earnings
    with patch("tools.check_earnings_calendar.yf.Ticker") as mock_ticker_cls:
        mock_t = MagicMock()
        mock_t.calendar = _make_earnings_df(45)
        mock_ticker_cls.return_value = mock_t
        results = get_upcoming_earnings(["PLTR"], days_ahead=30)
        assert len(results) == 0


def test_etf_skipped():
    from tools.check_earnings_calendar import get_upcoming_earnings
    with patch("tools.check_earnings_calendar.yf.Ticker") as mock_ticker_cls:
        results = get_upcoming_earnings(["VOO"], days_ahead=30)
        mock_ticker_cls.assert_not_called()
        assert results == []


def test_missing_calendar_handled_gracefully():
    from tools.check_earnings_calendar import get_upcoming_earnings
    with patch("tools.check_earnings_calendar.yf.Ticker") as mock_ticker_cls:
        mock_t = MagicMock()
        mock_t.calendar = pd.DataFrame()  # empty
        mock_ticker_cls.return_value = mock_t
        results = get_upcoming_earnings(["AMD"], days_ahead=30)
        assert results == []

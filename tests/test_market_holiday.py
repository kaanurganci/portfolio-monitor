from unittest.mock import patch, MagicMock
from datetime import date
import pandas as pd


def test_open_trading_day_returns_false():
    from tools.check_market_holiday import is_market_holiday
    with patch("tools.check_market_holiday.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_mcal.get_calendar.return_value = mock_cal
        # Non-empty schedule = market is open
        mock_cal.schedule.return_value = pd.DataFrame({"market_open": [1]})
        assert is_market_holiday(date(2026, 5, 13)) is False


def test_holiday_returns_true():
    from tools.check_market_holiday import is_market_holiday
    with patch("tools.check_market_holiday.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_mcal.get_calendar.return_value = mock_cal
        # Empty schedule = market closed
        mock_cal.schedule.return_value = pd.DataFrame()
        assert is_market_holiday(date(2026, 5, 26)) is True  # Memorial Day 2026


def test_weekend_returns_true():
    from tools.check_market_holiday import is_market_holiday
    with patch("tools.check_market_holiday.mcal") as mock_mcal:
        mock_cal = MagicMock()
        mock_mcal.get_calendar.return_value = mock_cal
        mock_cal.schedule.return_value = pd.DataFrame()
        assert is_market_holiday(date(2026, 5, 16)) is True  # Saturday

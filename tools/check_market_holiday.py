import pandas_market_calendars as mcal
from datetime import date
import sys


def is_market_holiday(check_date=None):
    if check_date is None:
        check_date = date.today()
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(
        start_date=str(check_date),
        end_date=str(check_date)
    )
    return len(schedule) == 0


if __name__ == "__main__":
    result = is_market_holiday()
    if result:
        print("CLOSED")
        sys.exit(1)
    else:
        print("OPEN")
        sys.exit(0)

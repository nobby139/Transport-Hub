from models import PayRate, SpecialDay
from utils.date_tools import is_good_friday

def get_pay_rate_band(duty_date):
    """
    Returns the correct PayRate band for the duty date.
    Each band represents ONE day-type:
    monfri / sat / sun / bankhol / special
    """
    day_type, special_code = get_day_type(duty_date)

    # Special day bands
    if day_type == "special":
        return PayRate.query \
            .filter(PayRate.day_type == "special") \
            .filter(PayRate.special_name == special_code) \
            .filter(PayRate.effective_from <= duty_date) \
            .order_by(PayRate.effective_from.desc()) \
            .first()

    # Normal day-type bands
    return PayRate.query \
        .filter(PayRate.day_type == day_type) \
        .filter(PayRate.effective_from <= duty_date) \
        .order_by(PayRate.effective_from.desc()) \
        .first()


def calculate_hourly_rate(duty_date, start_time):
    # Get correct band for THIS duty date
    rate_band = get_pay_rate_band(duty_date)

    # Determine day type
    day_type, special_code = get_day_type(duty_date)

    # Determine premium type (early, late, midlate, night)
    premium_type = get_premium_type(start_time)

    # SPECIAL DAY
    if day_type == "special":
        base_rate = rate_band.base_rate

        if premium_type:
            premium_amount = getattr(rate_band, f"{premium_type}_rate")
        else:
            premium_amount = 0

        return {
            "base_rate": base_rate,
            "premium_type": premium_type,
            "premium_amount": premium_amount,
            "hourly_rate": base_rate + premium_amount
        }

    # NORMAL DAY-TYPE
    base_rate = rate_band.base_rate

    if premium_type:
        premium_amount = getattr(rate_band, f"{premium_type}_rate")
    else:
        premium_amount = 0

    return {
        "base_rate": base_rate,
        "premium_type": premium_type,
        "premium_amount": premium_amount,
        "hourly_rate": base_rate + premium_amount
    }

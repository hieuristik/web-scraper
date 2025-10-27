"""
Helper function to calculate cost per point (cpp)
"""
def cpp_cents_per_point(cash_price_usd: float, taxes_fees_usd: float, points_required: int) -> float:
    if points_required <= 0:
        return 0.0
    cpp = (float(cash_price_usd) - float(taxes_fees_usd)) / float(points_required) * 100.0
    return round(cpp, 2)

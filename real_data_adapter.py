"""Future real-data adapter stubs.

The prototype currently uses local mock CSV files only. These functions define
the future integration boundary without making external API calls.
"""


def load_macro_data():
    """TODO: Load macro data from FRED / OECD / World Bank / IMF."""
    raise NotImplementedError("TODO: implement macro data adapter using FRED / OECD / World Bank / IMF")


def load_market_data():
    """TODO: Load market data from Bloomberg / Refinitiv / yfinance."""
    raise NotImplementedError("TODO: implement market data adapter using Bloomberg / Refinitiv / yfinance")


def load_consensus_data():
    """TODO: Load consensus data from surveys or economic calendar APIs."""
    raise NotImplementedError(
        "TODO: implement consensus adapter using Consensus Economics, analyst surveys, or economic calendar APIs"
    )


def load_real_estate_data():
    """TODO: Load real estate data from BIS residential property datasets."""
    raise NotImplementedError("TODO: implement real estate adapter using BIS Residential Property Price Index")

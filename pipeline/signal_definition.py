"""Stable signal-universe and schema definitions shared by pipeline stages."""

from pathlib import Path


SNAPSHOT_PATH = Path("snapshot.json")
NEWS_CACHE_PATH = Path(".cache") / "gdelt_news.json"
CONFIG_PATH = Path("signal_config.yaml")
DATA_DIR = Path("data")
METHODOLOGY_VERSION = "v0.2"
LIVE_HISTORY_YEARS = 8

ASSET_CLASSES = ("fx", "rates", "equity", "real_estate")
UNIVERSE = (
    "United States of America",
    "Canada",
    "China",
    "Japan",
    "Brazil",
    "Euro Area",
)
ISO3_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EUR",
}

REQUIRED_MACRO_COLUMNS = {
    "economy",
    "inflation_yoy",
    "gdp_growth",
    "unemployment",
    "policy_rate",
    "pmi",
}
REQUIRED_CONSENSUS_COLUMNS = {
    "economy",
    "inflation_consensus",
    "gdp_consensus",
    "unemployment_consensus",
    "policy_rate_consensus",
    "pmi_consensus",
}
REQUIRED_MARKET_COLUMNS = {
    "economy",
    "fx_3m_return",
    "fx_carry",
    "rate_3m_change",
    "curve_slope_2s10s",
    "equity_3m_return",
    "equity_forward_pe",
    "reit_3m_return",
    "house_price_yoy",
}
REQUIRED_NEWS_COLUMNS = {"economy", "news_pressure"}

SURPRISE_SPECS = (
    ("inflation_yoy", "inflation_consensus", "inflation_surprise"),
    ("gdp_growth", "gdp_consensus", "growth_surprise"),
    ("unemployment", "unemployment_consensus", "unemployment_surprise"),
    ("policy_rate", "policy_rate_consensus", "policy_surprise"),
    ("pmi", "pmi_consensus", "pmi_surprise"),
)

LIVE_MACRO_COLUMNS = ("inflation_yoy", "gdp_growth", "unemployment")
MARKET_LIVE_COLUMNS = ("fx_3m_return", "equity_3m_return")
INPUT_PROVENANCE_COLUMNS = tuple(
    sorted(
        (
            REQUIRED_MACRO_COLUMNS
            | REQUIRED_CONSENSUS_COLUMNS
            | REQUIRED_MARKET_COLUMNS
            | REQUIRED_NEWS_COLUMNS
        )
        - {"economy"}
    )
)

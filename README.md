# Cross-Asset Macro Dashboard

A runnable prototype macro dashboard for cross-asset signals across a small, explicit economy universe. The app uses mock data only, with a transparent deterministic signal engine and a hardcoded RAG/narrative signal stub.

## Architecture

- `main.py`: FastAPI backend
- `signal_engine.py`: loads mock CSV inputs, applies YAML-configured signal formulas, and writes `snapshot.json`
- `data_sources/world_bank.py`: live macro data adapter (World Bank API, no key); used when generation runs with `--source live`
- `data_sources/imf_weo.py`: IMF World Economic Outlook forecast adapter (DataMapper API, no key); supplies the live "consensus" so the live surprise becomes a forward expected-change
- `data_sources/market.py`: live market-return adapter (Yahoo Finance chart API, no key); sources `equity_3m_return` and `fx_3m_return` in `--source live`
- `regime_engine.py`: deterministic macro **regime-detection** engine (regime score, narrative gap, cross-asset confirmation, templated expressions/risks) for a separate six-economy set; writes `regime_snapshot.json`, served at `/api/regime` and shown in the dashboard's Regime tab. Verdict ladder: Deteriorating / Repricing / Early / Priced in / Neutral, where the activation verdicts (Repricing, Early) additionally require cross-asset `confirmation_score >= confirmation_min` (config: `regime_config.yaml`) — otherwise the verdict is **Unconfirmed**
- `rag_signal.py`: hardcoded qualitative narrative signal interface
- `real_data_adapter.py`: placeholder for future production data adapters
- `static/index.html`: vanilla HTML/JS dashboard using D3 and topojson
- `snapshot.json`: stable backend-to-frontend interface

No database, frontend framework, external data API, embedding call, or LLM call is used.

## Signal Methodology

The deterministic signal is a rule-based quantitative score from macro, market, and consensus-surprise inputs. Mock inputs live in:

- `data/mock_macro.csv`
- `data/mock_consensus.csv`
- `data/mock_market.csv`

Formula weights live in `signal_config.yaml`.

The engine computes surprises such as inflation, growth, unemployment, policy, and PMI surprises. Inputs are ranked cross-sectionally across the six economies, mapped to `[-1, +1]`, and combined into raw asset-class scores. Raw scores are ranked again cross-sectionally and mapped to `[-1, +1]`.

```text
signal = 2 * percentile_rank - 1
effective_rag_weight = rag_weight * rag_confidence          # rag_weight = 0.25
final_signal = (1 - effective_rag_weight) * deterministic_signal + effective_rag_weight * rag_signal
```

The RAG overlay is confidence-weighted: a full-confidence view uses the configured `rag_weight` (0.25), while a no-view / low-confidence cell collapses toward the deterministic signal. Each signal reports its `rag_effective_weight`.

The RAG signal is a qualitative narrative overlay returned by:

```python
compute_rag_signal(country, asset_class)
```

For now, it uses hardcoded scores and local mock snippets in `documents/`. Composite signals are equal-weight means of FX, rates, equity, and real estate.

## Universe

The six-economy signal universe is:

- United States of America
- Canada
- China
- Japan
- Brazil
- Euro Area

The map is global, but only these six economies have signals. Non-covered countries are visible as neutral no-data gray.

## Euro Area

Euro Area is a synthetic economy in the signal engine with `iso3 = "EUR"`. It is visualized by applying the same Euro Area signal to selected eurozone countries:

- Germany
- France
- Italy
- Spain
- Netherlands
- Belgium
- Austria
- Portugal
- Greece
- Finland
- Ireland

Clicking one of those countries shows both the map country and the synthetic Euro Area economy.

## Run

```bash
pip install -r requirements.txt
python signal_engine.py            # mock data (deterministic, offline)
python signal_engine.py --source live   # live World Bank macro data
python regime_engine.py            # rebuild regime_snapshot.json (regenerates on demand too)
uvicorn main:app --reload
```

If your shell exposes Python as `python3`, use `python3 signal_engine.py`.

Open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Test

```bash
pytest
```

## Current Limitations

- Live mode sources macro (inflation, GDP growth, unemployment) from the World Bank, consensus from IMF WEO, and the `equity_3m_return` / `fx_3m_return` market columns from Yahoo Finance. The remaining market columns (`fx_carry`, `rate_3m_change`, `curve_slope_2s10s`, `equity_forward_pe`, `reit_3m_return`, `house_price_yoy`), policy rate, and PMI remain mock. Each value's origin is recorded in `snapshot.json` under `provenance`.
- Consensus for live macro columns (inflation, GDP growth, unemployment) is the IMF WEO **next-year forecast**; the live "surprise" is the forecast-implied expected change, `forecast(T+1) - actual(T)`. It is an institutional forecast, not an intra-period analyst-consensus print. A column only switches to this expected-change mode when every economy has both a World Bank actual and an IMF forecast (all-or-nothing); otherwise it stays mock beat/miss. `policy_rate` and `pmi` have no live source, so their surprises always stay mock beat/miss.
- The only external API is the World Bank (live macro); market, consensus, and real estate have no live source yet
- RAG is hardcoded/stubbed
- Country mapping depends on world-atlas country names
- No historical time series snapshots yet

## TODO

- Extend live coverage to policy rate and PMI (needs keyed/proprietary sources)
- Extend live market data to carry, rates/curve, forward P/E, REIT, and real estate (BIS)
- Add real RAG pipeline with retrieval and citations
- Add historical time series snapshots

## Future Data Sources

- FRED / OECD / World Bank / IMF for macro data
- Bloomberg / Refinitiv / yfinance for market data
- Consensus Economics / analyst surveys / economic calendar APIs for consensus
- BIS Residential Property Price Index for real estate
- News API / central bank speeches / company filings / broker notes for RAG

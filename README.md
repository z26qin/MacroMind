# Cross-Asset Macro Dashboard

A runnable prototype macro dashboard for cross-asset signals across a small, explicit economy universe, built on a transparent deterministic signal engine and a point-in-time, cited narrative overlay. The signal engine runs in two modes: a fully offline mock-data mode (the default) and a live mode that pulls macro, consensus, market, and news-flow data from public no-key APIs (World Bank, IMF WEO, Yahoo Finance, GDELT). The committed `snapshot.json` is a live build — see [Run](#run) and [Current Limitations](#current-limitations) for what is live vs. still mock.

## Architecture

- `main.py`: FastAPI backend
- `signal_engine.py`: compatibility facade and CLI for the staged signal pipeline
- `pipeline/orchestrator.py`: explicit run coordinator from context/config through adapters, raw persistence, PIT selection, quality gates, features, and snapshot output
- `pipeline/stages/`: isolated input, feature-engineering, configuration, and snapshot-assembly stages
- `pipeline/store.py`: append-only SQLite raw observation ledger with revision-aware `query_as_of` retrieval
- `pipeline/quality.py`: versioned freshness, unit, admissible-range, model-ready coverage, and date-alignment gates; rejected live values fall back under the declared source policy
- `data_sources/world_bank.py`: live macro data adapter (World Bank API, no key); used when generation runs with `--source live`
- `data_sources/imf_weo.py`: IMF World Economic Outlook forecast adapter (DataMapper API, no key); supplies the live "consensus" so the live surprise becomes a forward expected-change
- `data_sources/market.py`: live market-return adapter (Yahoo Finance chart API, no key); sources `equity_3m_return` and `fx_3m_return` in `--source live`
- `data_sources/gdelt.py`: GDELT DOC 2.0 news-flow adapter (no key); builds an explainable `news_pressure` input from stress article flow minus constructive/relief article flow; live results are cached on disk at `.cache/gdelt_news.json` (per-economy, 6-hour TTL, git-ignored) so repeated live runs within the window skip the GDELT requests
- `evidence_store.py`: revision-aware SQLite evidence ledger; filters both `event_time` and `observed_at` for point-in-time retrieval and keeps country/asset/horizon dimensions
- `history.py`: builds a per-economy signal time series by reading every committed version of `snapshot.json` from git history; served at `/api/history` and drawn as a sparkline in the detail panel (requires running inside a git checkout)
- `regime_engine.py`: deterministic macro **regime-detection** engine (regime score, narrative gap, cross-asset confirmation, templated expressions/risks) for a separate six-economy set; writes `regime_snapshot.json`, served at `/api/regime` and shown in the dashboard's Regime tab. Verdict ladder: Deteriorating / Repricing / Early / Priced in / Neutral, where the activation verdicts (Repricing, Early) additionally require cross-asset `confirmation_score >= confirmation_min` (config: `regime_config.yaml`) — otherwise the verdict is **Unconfirmed**
- `rag_signal.py`: structured narrative extraction over point-in-time evidence; emits direction, factors, confidence, horizon, and revision-aware citations. The offline keyword extractor implements the same interface expected of a future LLM-backed extractor
- `evals/`: vendored retrieval, citation-grounding, point-in-time leakage, and confidence-calibration metrics plus the CI gate
- `eval_data/pit_narrative_golden_set.jsonl`: committed adjudicated seed cases for the end-to-end narrative gate
- `real_data_adapter.py`: placeholder for future production data adapters
- `static/index.html`: vanilla HTML/JS dashboard using D3 and topojson
- `snapshot.json`: stable backend-to-frontend interface

The evidence ledger uses local SQLite under `.cache/`. No frontend framework, embedding call, or hosted LLM call is required; the default structured extractor is deterministic so mock mode and CI remain fully offline. A production model can be injected through the `NarrativeExtractor` protocol without changing the snapshot contract or bypassing point-in-time retrieval.

## Signal Methodology

The deterministic signal is a rule-based quantitative score from macro, market, and consensus-surprise inputs. Mock inputs live in:

- `data/mock_macro.csv`
- `data/mock_consensus.csv`
- `data/mock_market.csv`
- `data/mock_news.csv`

Formula weights live in `signal_config.yaml`.

The engine computes surprises such as inflation, growth, unemployment, policy, and PMI surprises. It also computes a `news_pressure` input from GDELT article flow: stress terms such as inflation/recession/policy uncertainty/protests minus relief terms such as soft landing/disinflation/rate cuts/reform, scaled by total article flow. Inputs are ranked cross-sectionally across the six economies, mapped to `[-1, +1]`, and combined into raw asset-class scores. Raw scores are ranked again cross-sectionally and mapped to `[-1, +1]`.

```text
signal = 2 * percentile_rank - 1
effective_rag_weight = rag_weight * rag_confidence          # rag_weight = 0.25
final_signal = (1 - effective_rag_weight) * deterministic_signal + effective_rag_weight * rag_signal
```

The RAG overlay is confidence-weighted: a full-confidence view uses the configured `rag_weight` (0.25), while a no-view / low-confidence cell collapses toward the deterministic signal. Each signal reports its `rag_effective_weight`.

The narrative signal is a structured, cited overlay returned by:

```python
compute_rag_signal(country, asset_class)
```

The repository seeds six evidence records from `documents/`. Scores are derived from retrieved evidence rather than a country/asset score map. Every covered cell carries a `rag_analysis` block with direction, horizon, matched positive/negative factors, evidence count, and citations containing `event_time`, `observed_at`, source, revision, and vintage. No evidence produces `confidence = 0`, so an uncovered narrative cannot dilute the deterministic signal. Composite signals are equal-weight means of FX, rates, equity, and real estate.

### Point-in-time evidence

Each evidence revision requires:

- `event_time` and `observed_at` as timezone-aware ISO-8601 timestamps
- `source`, `revision`, and `vintage`
- `country`, `asset`, and `horizon`
- an `evidence_id`, title, content, and citation URI

The same `evidence_id` may have multiple revisions. A query returns the latest revision that was actually observed by the decision timestamp; later revisions remain invisible. Country-level `macro` and `cross_asset` evidence can also be retrieved for a specific asset.

### Live observation quality gates

Live adapters always write their complete `SourceBatch` results to the append-only raw store. The orchestrator then performs PIT retrieval and runs quality policy `v1` before any live value can enter a signal:

- **Freshness:** latest model candidates are limited to 730 days for World Bank annual actuals, 240 days by IMF observation time, 45 days for Yahoo monthly market bars, and 2 days for GDELT.
- **Unit and range:** every live metric has an exact unit contract and a broad admissible range; failures reject that observation without rewriting raw history.
- **Date alignment:** realized periods cannot end after the decision time, and IMF forecasts must match the quality-approved World Bank actual year plus one.
- **Coverage:** coverage is recalculated after the other gates. World Bank retains approved countries per cell; IMF, Yahoo, and GDELT fall back for the whole metric unless the six-economy cross-section is complete.

Gate outcomes are returned on `PipelineResult.quality`, including accepted/blocked counts, per-gate issues, and the coverage decisions. Acquisition coverage remains separately available on `PipelineResult.coverage`.

### Conviction

Each asset signal also carries a deterministic **conviction** read (`signals.<asset>.conviction` in `snapshot.json`) answering "how trustworthy is this call":

- **Breadth** — `net_lean ∈ [−1, +1]`, the weight-aligned agreement of the drivers with the deterministic call direction (negative = the drivers point against the call, which then rests purely on the cross-sectional ranking), plus `top_driver_share` (concentration on a single driver).
- **Narrative agreement** — whether the RAG overlay agrees with the deterministic call. Asymmetric: disagreement lowers the band, agreement never raises it (the RAG overlay is a stub).

These roll up to a `band` of `high` / `medium` / `low`, or `na` for a Neutral signal. The dashboard shows the band as a chip in the detail panel (with the raw math behind a "Show math" disclosure) and in the map/heatmap hover. Composite signals carry no conviction in this version.

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

Python 3.11 or newer is required.

```bash
pip install -r requirements.txt
python signal_engine.py            # mock data (deterministic, offline)
python signal_engine.py --source live   # live World Bank macro data
python regime_engine.py            # rebuild regime_snapshot.json (regenerates on demand too)
python evidence_store.py ingest evidence/seed.jsonl
python evidence_store.py query --country Japan --asset fx --horizon 3m --as-of 2026-06-02T23:59:59Z
uvicorn main:app --reload
```

If your shell exposes Python as `python3`, use `python3 signal_engine.py`.

Open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Test

```bash
pytest
python -m evals.ci
python -m evals.report
```

## Current Limitations

- Live mode sources macro (inflation, GDP growth, unemployment) from the World Bank, consensus from IMF WEO, the `equity_3m_return` / `fx_3m_return` market columns from Yahoo Finance, and `news_pressure` from GDELT. `fx_carry` is **derived** in live mode as the policy-rate differential vs the US (carry = local short rate − USD short rate); since policy rates are still mock, this is *live-ready* rather than live — it becomes genuinely live once policy rates get a source. The remaining market columns (`rate_3m_change`, `curve_slope_2s10s`, `equity_forward_pe`, `reit_3m_return`, `house_price_yoy`), policy rate, and PMI remain mock. Each value's origin is recorded in `snapshot.json` under `provenance`.
- Consensus for live macro columns (inflation, GDP growth, unemployment) is the IMF WEO **next-year forecast**; the live "surprise" is the forecast-implied expected change, `forecast(T+1) - actual(T)`. It is an institutional forecast, not an intra-period analyst-consensus print. A column only switches to this expected-change mode when every economy has both a World Bank actual and an IMF forecast (all-or-nothing); otherwise it stays mock beat/miss. `policy_rate` and `pmi` have no live source, so their surprises always stay mock beat/miss.
- Live external sources are the World Bank (macro), IMF WEO (consensus), Yahoo Finance (FX/equity returns), and GDELT (news pressure); policy rate, PMI, and real estate have no live source yet
- The GDELT news-pressure overlay caches scores per economy on disk (`.cache/gdelt_news.json`, 6-hour TTL). The cache stores only successful fetches, so a partially-failed run refetches just the missing economies next time; delete the file to force a full refresh.
- The default narrative extractor is an offline keyword baseline. The point-in-time retrieval, structured output contract, and citation checks are ready for an LLM-backed extractor, but no hosted model provider is configured yet.
- Country mapping depends on world-atlas country names

## TODO

- Extend live coverage to policy rate and PMI (needs keyed/proprietary sources)
- Extend live market data to rates/curve, forward P/E, REIT, and real estate (BIS)
- Source live policy rates (would also make `fx_carry` genuinely live)
- Add production evidence connectors and an LLM-backed `NarrativeExtractor`
- Expand the golden set with historical PM-adjudicated outcomes by asset, horizon, and regime

## Future Data Sources

- FRED / OECD / World Bank / IMF for macro data
- Bloomberg / Refinitiv / yfinance for market data
- Consensus Economics / analyst surveys / economic calendar APIs for consensus
- BIS Residential Property Price Index for real estate
- News API / central bank speeches / company filings / broker notes for RAG

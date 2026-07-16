"""Pure diff engine between two archived snapshots.

Input shape per side (from snapshot_store.load_snapshot):
    {"id": str, "signal": <snapshot.json dict>, "regime": <regime_snapshot.json dict>}

Change levels:
    L1 headline   — regime verdict flips, signal direction flips
    L2 rank moves — opportunity-board / per-cell cross-sectional rank moves
    L3 drift      — numeric moves >= DRIFT_MIN
    L4 context    — evidence changes, provenance live<->fallback flips, coverage

v1 thresholds are hand-picked starting points; revisit after real use.
"""

from __future__ import annotations

SIGN_FLIP_BAND = 0.15
DRIFT_MIN = 0.10
RANK_MOVE_MIN = 2
RAG_CONF_MIN = 0.20
TOP_N = 3

ASSET_KEYS = ("fx", "rates", "equity", "real_estate")
# composite is the dashboard's landing view, so it takes part in flips,
# drift, and rank moves alongside the four asset-class cells.
CELL_KEYS = ASSET_KEYS + ("composite",)


def _cell_final(economy: dict, cell: str):
    block = (
        economy.get("composite")
        if cell == "composite"
        else (economy.get("signals") or {}).get(cell)
    )
    if not isinstance(block, dict):
        return None
    return block.get("final")


def _is_live(provenance_value) -> bool:
    return isinstance(provenance_value, str) and not provenance_value.startswith(
        ("mock", "derived")
    )


def opportunity_ranking(regime: dict) -> list[str]:
    """Gap desc, confirmation desc — the one ranking rule, mirrored by the UI."""
    ranked = sorted(
        regime.get("countries", []),
        key=lambda c: (
            -(c.get("narrative_gap") or 0.0),
            -(c.get("confirmation_score") or 0.0),
        ),
    )
    return [c["country"] for c in ranked]


def _asset_ranking(signal: dict, cell: str) -> list[str]:
    rows = []
    for name, economy in (signal.get("economies") or {}).items():
        value = _cell_final(economy, cell)
        if value is not None:
            rows.append((name, value))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _ in rows]


def compute_diff(base: dict, target: dict) -> dict:
    changes: list[dict] = []
    notes: list[str] = []
    minor_count = 0
    touched: set[str] = set()  # countries with at least one change entry
    drifted: set[str] = set()  # countries with sub-threshold numeric deltas

    base_sig, target_sig = base["signal"], target["signal"]
    base_reg, target_reg = base["regime"], target["regime"]
    result = {
        "base": {"id": base["id"], "as_of": base_sig.get("as_of")},
        "target": {"id": target["id"], "as_of": target_sig.get("as_of")},
    }

    if base_sig.get("methodology_version") != target_sig.get("methodology_version") or (
        base_reg.get("methodology_version") != target_reg.get("methodology_version")
    ):
        notes.append(
            "methodology_version changed between snapshots; "
            "value diff suppressed as not comparable"
        )
        return {**result, "changes": [], "minor_count": 0, "unchanged_count": 0, "notes": notes}

    def add(level, kind, country, headline, **fields):
        entry = {"level": level, "kind": kind, "country": country, "headline": headline}
        entry.update(fields)
        changes.append(entry)
        touched.add(country)

    def crossed(country, delta, threshold):
        """True when |delta| passes threshold; otherwise counts toward minor."""
        nonlocal minor_count
        if abs(delta) >= threshold:
            return True
        if delta != 0:
            minor_count += 1
            drifted.add(country)
        return False

    # ---- regime: verdict flips (L1) + drift (L3) + coverage (L4) ----
    base_countries = {c["country"]: c for c in base_reg.get("countries", [])}
    target_countries = {c["country"]: c for c in target_reg.get("countries", [])}
    for name in sorted(set(base_countries) | set(target_countries)):
        b, t = base_countries.get(name), target_countries.get(name)
        if b is None or t is None:
            side = "left regime universe" if t is None else "entered regime universe"
            add(4, "coverage_change", name, f"{name}: {side}",
                **{"from": b is not None, "to": t is not None})
            continue
        if b.get("verdict") != t.get("verdict"):
            add(
                1, "verdict_flip", name,
                f"{name}: {b.get('verdict')} → {t.get('verdict')}",
                **{"from": b.get("verdict"), "to": t.get("verdict")},
                detail={
                    "narrative_gap": {"from": b.get("narrative_gap"), "to": t.get("narrative_gap")},
                    "confirmation_score": {
                        "from": b.get("confirmation_score"),
                        "to": t.get("confirmation_score"),
                    },
                },
            )
        for field in ("regime_score", "narrative_gap", "confirmation_score"):
            bv, tv = b.get(field), t.get(field)
            if bv is None or tv is None:
                continue
            if crossed(name, tv - bv, DRIFT_MIN):
                add(3, "regime_drift", name,
                    f"{name}: {field} {bv:+.2f} → {tv:+.2f}",
                    field=field, **{"from": bv, "to": tv})

    # ---- signal: direction flips (L1) + drift (L3) + coverage (L4) ----
    base_econ = base_sig.get("economies") or {}
    target_econ = target_sig.get("economies") or {}
    for name in sorted(set(base_econ) | set(target_econ)):
        b, t = base_econ.get(name), target_econ.get(name)
        if b is None or t is None:
            side = "left signal universe" if t is None else "entered signal universe"
            add(4, "coverage_change", name, f"{name}: {side}",
                **{"from": b is not None, "to": t is not None})
            continue
        for cell in CELL_KEYS:
            bv, tv = _cell_final(b, cell), _cell_final(t, cell)
            if bv is None or tv is None:
                continue
            flipped = (bv <= -SIGN_FLIP_BAND and tv >= SIGN_FLIP_BAND) or (
                bv >= SIGN_FLIP_BAND and tv <= -SIGN_FLIP_BAND
            )
            if flipped:
                add(1, "direction_flip", name,
                    f"{name} {cell}: {bv:+.2f} → {tv:+.2f}",
                    field=cell, **{"from": bv, "to": tv})
            elif crossed(name, tv - bv, DRIFT_MIN):
                add(3, "signal_drift", name,
                    f"{name} {cell}: {bv:+.2f} → {tv:+.2f}",
                    field=cell, **{"from": bv, "to": tv})

    _add_rank_moves(base_sig, target_sig, base_reg, target_reg, add)
    _add_context_changes(base_econ, target_econ, add, crossed)

    changes.sort(key=lambda c: (c["level"], c["country"], c["kind"], str(c.get("field") or "")))
    all_names = set(base_countries) | set(target_countries) | set(base_econ) | set(target_econ)
    unchanged_count = len(all_names - touched - drifted)
    return {**result, "changes": changes, "minor_count": minor_count,
            "unchanged_count": unchanged_count, "notes": notes}


def _add_rank_moves(base_sig, target_sig, base_reg, target_reg, add):
    """L2 rank moves — implemented in Task 3."""


def _add_context_changes(base_econ, target_econ, add, crossed):
    """L4 evidence + provenance — implemented in Task 3."""

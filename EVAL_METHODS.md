# MacroMind Evaluation Methods

The evaluation package is vendored under `evals/` and runs without an external
checkout. Print the live method catalog, including implementation source, with:

```bash
python -m evals.report
```

Run the merge/refresh quality gate with:

```bash
python -m evals.ci
```

| Method | Axis | Module |
|---|---|---|
| `hit_rate_at_k` | Retrieval | `evals.retrieval_metrics` |
| `recall_at_k` | Retrieval | `evals.retrieval_metrics` |
| `mrr` | Retrieval | `evals.retrieval_metrics` |
| `ndcg_at_k` | Retrieval | `evals.retrieval_metrics` |
| `find_lookahead_violations` | Point-in-time | `evals.pit` |
| `assert_no_lookahead` | Point-in-time | `evals.pit` |
| `grounding_rate` | Citation grounding | `evals.grounding` |
| `faithfulness` | Generation | `evals.ragas_metrics` |
| `answer_relevancy` | Generation | `evals.ragas_metrics` |
| `brier_score` | Confidence calibration | `evals.calibration` |
| `expected_calibration_error` | Confidence calibration | `evals.calibration` |

The end-to-end gate evaluates the committed golden set in
`eval_data/pit_narrative_golden_set.jsonl`. It requires perfect retrieval and citation
grounding on this small seed set, rejects any evidence visible before its event
or observation time, and bounds both Brier score and expected calibration
error. A future-dated canary is injected during every run to catch a broken
point-in-time filter.

These seed thresholds are intentionally strict for deterministic invariants but
are not evidence of investment efficacy. Production evaluation should extend
the set with historical, adjudicated PM decisions and report results by asset,
horizon, regime, source, and vintage.

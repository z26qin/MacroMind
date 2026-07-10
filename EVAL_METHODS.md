# Fed-Thesis Eval Methods

Auto-generated catalog of every eval method added to this repo (`python -m evals.report`). Each entry maps to the eval guide's axis and embeds the real source — it cannot drift from the code.

| # | Method | Axis | Needs LLM? | Module |
|---|---|---|---|---|
| 1 | `hit_rate_at_k` | Retrieval · IR (§1.2) | no | `evals.retrieval_metrics` |
| 2 | `recall_at_k` | Retrieval · IR (§1.2) | no | `evals.retrieval_metrics` |
| 3 | `mrr` | Retrieval · IR (§1.2) | no | `evals.retrieval_metrics` |
| 4 | `ndcg_at_k` | Retrieval · IR (§1.2) | no | `evals.retrieval_metrics` |
| 5 | `find_lookahead_violations` | Retrieval · Point-in-time (§3.1) | no | `evals.pit` |
| 6 | `assert_no_lookahead` | Retrieval · Point-in-time (§3.1) | no | `evals.pit` |
| 7 | `grounding_rate` | Generation · Evidence grounding (§3.4) | no | `evals.grounding` |
| 8 | `faithfulness` | Generation · Faithfulness (§1.1) | yes (Judge) | `evals.ragas_metrics` |
| 9 | `answer_relevancy` | Generation · Answer relevancy (§1.1) | yes (Judge) | `evals.ragas_metrics` |

## `hit_rate_at_k`  —  Retrieval · IR (§1.2)
_module: `evals.retrieval_metrics`_

1.0 if any gold id appears in the top-k, else 0.0 (a.k.a. Recall@k>0).

```python
def hit_rate_at_k(retrieved_ids: Sequence, gold_ids: Set, k: int) -> float:
    """1.0 if any gold id appears in the top-k, else 0.0 (a.k.a. Recall@k>0)."""
    topk = list(retrieved_ids)[:k]
    return 1.0 if any(i in gold_ids for i in topk) else 0.0
```

## `recall_at_k`  —  Retrieval · IR (§1.2)
_module: `evals.retrieval_metrics`_

Fraction of gold ids present in the top-k. 0.0 if no gold ids.

```python
def recall_at_k(retrieved_ids: Sequence, gold_ids: Set, k: int) -> float:
    """Fraction of gold ids present in the top-k. 0.0 if no gold ids."""
    if not gold_ids:
        return 0.0
    topk = set(list(retrieved_ids)[:k])
    return len(topk & gold_ids) / len(gold_ids)
```

## `mrr`  —  Retrieval · IR (§1.2)
_module: `evals.retrieval_metrics`_

Mean reciprocal rank across many (retrieved_ids, gold_ids) pairs.

```python
def mrr(rankings: Iterable) -> float:
    """Mean reciprocal rank across many (retrieved_ids, gold_ids) pairs."""
    rrs = [reciprocal_rank(r, g) for r, g in rankings]
    return sum(rrs) / len(rrs) if rrs else 0.0
```

## `ndcg_at_k`  —  Retrieval · IR (§1.2)
_module: `evals.retrieval_metrics`_

DCG normalized by ideal DCG. Rewards putting the gold chunk first —
burying the answer at position 5 degrades downstream generation.

```python
def ndcg_at_k(retrieved_ids: Sequence, gold_ids: Set, k: int) -> float:
    """DCG normalized by ideal DCG. Rewards putting the gold chunk first —
    burying the answer at position 5 degrades downstream generation."""
    ideal_hits = min(len(gold_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(retrieved_ids, gold_ids, k) / idcg
```

## `find_lookahead_violations`  —  Retrieval · Point-in-time (§3.1)
_module: `evals.pit`_

Return every record whose event_time is >= the decision timestamp.

```python
def find_lookahead_violations(records: Sequence, decision_time_iso: str) -> list:
    """Return every record whose event_time is >= the decision timestamp."""
    cutoff = _to_unix(decision_time_iso)
    out = []
    for r in records:
        et = r.get("event_time", "")
        if not et:
            continue
        if _to_unix(et) >= cutoff:
            out.append(LeakViolation(r.get("event_id", "?"), et, decision_time_iso))
    return out
```

## `assert_no_lookahead`  —  Retrieval · Point-in-time (§3.1)
_module: `evals.pit`_

Raise AssertionError on the first look-ahead leak. Use in CI gates.

```python
def assert_no_lookahead(records: Sequence, decision_time_iso: str) -> None:
    """Raise AssertionError on the first look-ahead leak. Use in CI gates."""
    violations = find_lookahead_violations(records, decision_time_iso)
    assert not violations, (
        f"PIT look-ahead leakage: {len(violations)} future-dated record(s) "
        f"retrieved for decision @ {decision_time_iso}: "
        f"{[(v.event_id, v.event_time) for v in violations]}"
    )
```

## `grounding_rate`  —  Generation · Evidence grounding (§3.4)
_module: `evals.grounding`_

Fraction of key_phrases that are verbatim-grounded in the source.

```python
def grounding_rate(key_phrases: Sequence, source_text: str) -> float:
    """Fraction of key_phrases that are verbatim-grounded in the source."""
    phrases = [p for p in key_phrases if p and p.strip()]
    if not phrases:
        return 0.0
    grounded = sum(1 for p in phrases if phrase_is_grounded(p, source_text))
    return grounded / len(phrases)
```

## `faithfulness`  —  Generation · Faithfulness (§1.1)
_module: `evals.ragas_metrics`_

Fraction of the answer's atomic claims entailed by the context.

Low faithfulness + high context recall -> generation problem (the context
was there; the model confabulated). Low faithfulness + low recall ->
retrieval problem (see harness diagnostics).

```python
def faithfulness(answer: str, context: str, judge: Judge) -> float:
    """Fraction of the answer's atomic claims entailed by the context.

    Low faithfulness + high context recall -> generation problem (the context
    was there; the model confabulated). Low faithfulness + low recall ->
    retrieval problem (see harness diagnostics)."""
    claims = decompose_claims(answer)
    if not claims:
        return 0.0
    supported = sum(1 for c in claims if judge.entails(c, context))
    return supported / len(claims)
```

## `answer_relevancy`  —  Generation · Answer relevancy (§1.1)
_module: `evals.ragas_metrics`_

How directly the answer addresses the question (0..1). Catches the
'faithfully answering the WRONG question' failure (guide §1.1).

```python
def answer_relevancy(question: str, answer: str, judge: Judge) -> float:
    """How directly the answer addresses the question (0..1). Catches the
    'faithfully answering the WRONG question' failure (guide §1.1)."""
    if not answer.strip():
        return 0.0
    return judge.relevance(question, answer)
```

## Library-backed metrics (Part 2 — optional, `EVAL_BACKEND` switch)

| Backend | Entry point | Metrics added | Judge |
|---|---|---|---|
| `ragas` | `evals.adapters.ragas_adapter.evaluate_samples` | faithfulness, answer_relevancy, **context_precision**, **context_recall** | Claude Sonnet |
| `deepeval` | `evals.adapters.deepeval_adapter.assert_samples_pass` | faithfulness, answer_relevancy (pytest gate w/ thresholds) | Claude Sonnet |
| LangSmith | `evals.adapters.langsmith_adapter.push_golden_set` | dataset mgmt + run tracing (no new metric) | n/a |

Switch with `EVAL_BACKEND=ragas|deepeval` (default `pure`); enable tracing with `LANGSMITH_TRACING=true`. Heavy deps live in `venv-eval` (Python 3.11); the core gate stays in the 3.9 venv.

## Diagnostic logic (why measure both axes separately)

| Signal | Diagnosis | Fix layer |
|---|---|---|
| low `recall@k` + low `faithfulness` | retrieval problem | chunking / embeddings / top-k / rerank |
| high `recall@k` + low `faithfulness` | generation problem | prompt / model / grounding constraint |
| high `faithfulness` + low `answer_relevancy` | answering the wrong question | query understanding |
| any `pit_leakage_rate > 0` | look-ahead leakage | retriever date filter — block merge |

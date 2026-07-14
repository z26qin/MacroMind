"""Print the vendored MacroMind evaluation-method catalog."""
from __future__ import annotations

import inspect

from . import calibration, grounding, pit, ragas_metrics, retrieval_metrics


METHODS = (
    ("Retrieval", retrieval_metrics.hit_rate_at_k),
    ("Retrieval", retrieval_metrics.recall_at_k),
    ("Retrieval", retrieval_metrics.mrr),
    ("Retrieval", retrieval_metrics.ndcg_at_k),
    ("Point-in-time", pit.find_lookahead_violations),
    ("Point-in-time", pit.assert_no_lookahead),
    ("Grounding", grounding.grounding_rate),
    ("Generation", ragas_metrics.faithfulness),
    ("Generation", ragas_metrics.answer_relevancy),
    ("Calibration", calibration.brier_score),
    ("Calibration", calibration.expected_calibration_error),
)


def render() -> str:
    lines = [
        "# MacroMind Evaluation Methods",
        "",
        "| Method | Axis | Module |",
        "|---|---|---|",
    ]
    for axis, method in METHODS:
        lines.append(f"| `{method.__name__}` | {axis} | `{method.__module__}` |")
    lines.extend(["", "## Source", ""])
    for axis, method in METHODS:
        lines.extend(
            [
                f"### `{method.__name__}` — {axis}",
                "",
                "```python",
                inspect.getsource(method).rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(render())

"""Callable contract implemented by every live data adapter."""

from __future__ import annotations

from typing import Protocol

from pipeline.contracts import PipelineRunContext, SourceBatch


class LiveAdapter(Protocol):
    def __call__(
        self,
        context: PipelineRunContext,
        economies: tuple[str, ...],
    ) -> SourceBatch: ...

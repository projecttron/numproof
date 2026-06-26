# SPDX-License-Identifier: MIT
"""NumProof custom metric for DeepEval (Confident AI's LLM-eval framework).

A deterministic DeepEval metric that scores an LLM output by running its numeric/
financial claim through the hosted NumProof verifier — no judge model, no flakiness.

Mapping convention (shared across all NumProof framework integrations)::

    VERIFY   -> score 1.0  (the claim provably holds)
    REFUTE   -> score 0.0  (the claim is provably false; reason carries the counterexample)
    ABSTAIN  -> configurable; default = score 1.0 but flagged (NumProof could not decide /
                the statement is not a checkable numeric claim — this is *not* a refutation)

Why a custom metric (not G-Eval)
--------------------------------
DeepEval's built-in correctness metrics (``GEval``, ``AnswerRelevancy`` ...) ask *another*
LLM to judge the answer, so they are themselves non-deterministic and can rubber-stamp a
wrong number. :class:`NumProofMetric` instead asks the NumProof engine for a *proof or a
counterexample*, so the metric ``score`` is reproducible and CI-stable.

What it checks
--------------
The metric verifies the claim found in the test case. By default the claim is the test
case's ``actual_output`` (the model's answer). If you stored the precise statement to
check elsewhere — e.g. ``LLMTestCase(..., additional_metadata={"claim": "..."})`` — that
takes precedence. Override extraction entirely with ``claim_extractor=``.

Usage
-----
Run it like any other DeepEval metric — standalone, in ``assert_test``, or in
``evaluate`` (and, when you're logged in with ``deepeval login``, the results stream to
the Confident AI dashboard)::

    from deepeval.test_case import LLMTestCase
    from numproof.integrations.deepeval import NumProofMetric

    metric = NumProofMetric()                       # NumProof.from_env() under the hood
    tc = LLMTestCase(
        input="What is 120 + 90 + 340 + 15?",
        actual_output="The total is 565.",
    )
    metric.measure(tc)
    print(metric.score, metric.success, metric.reason)

    # pytest-style:
    #   from deepeval import assert_test
    #   assert_test(tc, [metric])
    #
    # bulk / dashboard:
    #   from deepeval import evaluate
    #   evaluate(test_cases=[tc], metrics=[metric])

Design notes
------------
* No verification logic lives here. We only call the hosted NumProof engine through the
  official stdlib-only :class:`numproof.NumProof` client (see ``numproof/client.py``).
* ``deepeval`` is an *optional* dependency, imported **lazily inside the factory**, so
  importing ``numproof`` or ``numproof.integrations`` never requires DeepEval. The real
  ``BaseMetric`` subclass is built (and cached) the first time you instantiate
  ``NumProofMetric`` — ``NumProofMetric(...)`` returns a genuine ``BaseMetric`` instance
  that DeepEval's runner accepts via its ``isinstance(metric, BaseMetric)`` check.
* Verified against the current (2026) DeepEval ``BaseMetric`` contract:
    - ``from deepeval.metrics import BaseMetric``; ``from deepeval.test_case import LLMTestCase``
    - implement ``measure(self, test_case)`` and ``async a_measure(self, test_case)``
    - set ``self.score`` / ``self.reason`` / ``self.success`` (vs ``self.threshold``),
      ``self.error`` on failure, and expose ``is_successful()`` + the ``__name__`` property.
  ``BaseMetric`` declares class-level defaults for ``score``/``reason``/``success``/
  ``error``/``evaluation_cost``, so the subclass ``__init__`` need not call
  ``super().__init__()``.

MIT License. Copyright (c) NumProof contributors.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from numproof import NumProof

# Type-only import so static checkers know the test-case type without forcing DeepEval to
# be importable at module-import time.
if TYPE_CHECKING:  # pragma: no cover
    from deepeval.test_case import LLMTestCase

__all__ = [
    "NumProofMetric",
    "default_claim_extractor",
    "VERIFY",
    "REFUTE",
    "ABSTAIN",
]

# Verdict constants (match the hosted engine's response ``verdict`` field).
VERIFY = "VERIFY"
REFUTE = "REFUTE"
ABSTAIN = "ABSTAIN"


def default_claim_extractor(test_case: Any) -> str:
    """Pull the claim string to verify out of a DeepEval ``LLMTestCase``.

    Precedence:
      1. ``test_case.additional_metadata["claim"]`` (or ``["expected_claim"]`` /
         ``["numproof_claim"]``) if present — lets you pin the exact statement to check
         independently of the model's phrasing.
      2. ``test_case.actual_output`` — the model's answer (the common case).

    The whole string is handed to NumProof, whose engine parses natural-language
    numeric/financial statements and decides what (if anything) is checkable. Replace this
    with your own ``claim_extractor=`` to, e.g., read a structured field or split sentences.
    """
    meta = getattr(test_case, "additional_metadata", None)
    if isinstance(meta, dict):
        for key in ("claim", "expected_claim", "numproof_claim"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    output = getattr(test_case, "actual_output", None)
    return output.strip() if isinstance(output, str) else ""


def _build_reason(claim: str, result: dict[str, Any]) -> str:
    """Human-readable explanation surfaced on ``self.reason`` (and in DeepEval reports)."""
    verdict = str(result.get("verdict", ABSTAIN)).upper()
    detail = result.get("detail")
    if verdict == VERIFY:
        msg = f"NumProof VERIFIED the numeric claim {claim!r}: it provably holds."
        return msg + (f" {detail}" if detail else "")
    if verdict == REFUTE:
        cex = result.get("counterexample")
        msg = f"NumProof REFUTED the numeric claim {claim!r}: it is provably false."
        if detail:
            msg += f" {detail}."
        if cex:
            msg += f" Counterexample: {cex}."
        return msg
    # ABSTAIN / unknown.
    msg = (
        f"NumProof ABSTAINED on {claim!r}: it is not a decidable numeric/financial claim "
        f"(this is not a refutation)."
    )
    return msg + (f" {detail}" if detail else "")


# The real BaseMetric subclass is built once, lazily, on first construction and cached here.
_NumProofMetricImpl: Optional[type] = None


def _build_metric_class() -> type:
    """Define the concrete ``BaseMetric`` subclass (cached). Imports DeepEval lazily."""
    global _NumProofMetricImpl
    if _NumProofMetricImpl is not None:
        return _NumProofMetricImpl

    try:
        from deepeval.metrics import BaseMetric  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only without deepeval
        raise ImportError(
            "NumProofMetric requires the 'deepeval' package. Install it with:\n"
            "    pip install deepeval\n"
            "or:\n"
            "    pip install 'numproof[deepeval]'"
        ) from exc

    class _NumProofMetric(BaseMetric):  # type: ignore[misc, valid-type]
        """Deterministic DeepEval metric backed by the hosted NumProof verifier."""

        def __init__(
            self,
            client: Optional[NumProof] = None,
            *,
            threshold: float = 0.5,
            claim_extractor: Callable[[Any], str] = default_claim_extractor,
            abstain_score: float = 1.0,
            strict_abstain: bool = False,
            empty_score: float = 1.0,
            allow_llm: bool = False,
            include_reason: bool = True,
            async_mode: bool = True,
            strict_mode: bool = False,
        ) -> None:
            self._client = client
            self._claim_extractor = claim_extractor
            self._abstain_score = 0.0 if strict_abstain else float(abstain_score)
            self._empty_score = float(empty_score)
            self._allow_llm = allow_llm

            # DeepEval-recognised attributes (BaseMetric supplies class-level defaults for
            # score/reason/success/error, so we just set the configurable ones here).
            self.threshold = 1.0 if strict_mode else float(threshold)
            self.include_reason = include_reason
            self.async_mode = async_mode
            self.strict_mode = strict_mode
            self.evaluation_cost = 0.0  # deterministic engine; no LLM token cost
            self.score = None
            self.reason = None
            self.success = None
            self.error = None

        # ---- the metric contract ------------------------------------------------------ #

        def measure(self, test_case: "LLMTestCase", *args: Any, **kwargs: Any) -> float:
            """Verify the test case's claim; set ``score`` / ``reason`` / ``success``."""
            try:
                client = self._client or NumProof.from_env()
                claim = (self._claim_extractor(test_case) or "").strip()

                if not claim:
                    self.score = self._empty_score
                    if self.include_reason:
                        self.reason = (
                            "No numeric claim found in the test case to verify "
                            "(nothing to refute)."
                        )
                    self.success = self.score >= self.threshold
                    self.error = None
                    return self.score

                result = client.verify(claim, allow_llm=self._allow_llm)
                verdict = str(result.get("verdict", ABSTAIN)).upper()

                if verdict == VERIFY:
                    self.score = 1.0
                elif verdict == REFUTE:
                    self.score = 0.0
                else:  # ABSTAIN or any unknown verdict -> not a refutation
                    self.score = self._abstain_score

                if self.include_reason:
                    self.reason = _build_reason(claim, result)
                self.success = self.score >= self.threshold
                self.error = None
                return self.score
            except Exception as exc:  # network/API error -> mark errored (DeepEval convention)
                self.error = str(exc)
                raise

        async def a_measure(
            self, test_case: "LLMTestCase", *args: Any, **kwargs: Any
        ) -> float:
            """Async entry point. The NumProof call is synchronous HTTP, so we reuse
            :meth:`measure` (DeepEval's documented fallback when there is no async path)."""
            return self.measure(test_case, *args, **kwargs)

        def is_successful(self) -> bool:
            """Pass/fail flag DeepEval reads after ``measure`` / ``a_measure``."""
            if self.error is not None:
                self.success = False
            else:
                try:
                    self.success = bool(self.score >= self.threshold)
                except TypeError:
                    self.success = False
            return self.success

        @property
        def __name__(self) -> str:  # shown in DeepEval reports / Confident AI dashboard
            return "NumProof (deterministic numeric verification)"

    _NumProofMetricImpl = _NumProofMetric
    return _NumProofMetricImpl


def NumProofMetric(
    client: Optional[NumProof] = None,
    *,
    threshold: float = 0.5,
    claim_extractor: Callable[[Any], str] = default_claim_extractor,
    abstain_score: float = 1.0,
    strict_abstain: bool = False,
    empty_score: float = 1.0,
    allow_llm: bool = False,
    include_reason: bool = True,
    async_mode: bool = True,
    strict_mode: bool = False,
) -> Any:
    """Build a deterministic DeepEval metric backed by the hosted NumProof verifier.

    Returns a genuine ``deepeval.metrics.BaseMetric`` instance (the concrete subclass is
    defined lazily on first call, so importing this module never requires DeepEval). Scores
    a test case by verifying its numeric claim:

    ====================  =====================================================
    NumProof verdict      metric outcome
    ====================  =====================================================
    VERIFY                ``score = 1.0`` -> success (claim proven correct)
    REFUTE                ``score = 0.0`` -> failure (``reason`` = counterexample)
    ABSTAIN               ``score = abstain_score`` (default 1.0); ``strict_abstain``
                          flips it to 0.0 ("if we can't prove it, fail it")
    no checkable claim    ``score = empty_score`` (default 1.0; nothing to refute)
    ====================  =====================================================

    Parameters
    ----------
    client:
        A :class:`numproof.NumProof` instance. Defaults to ``NumProof.from_env()`` (reads
        ``NUMPROOF_BASE_URL`` / ``NUMPROOF_API_KEY``) at ``measure`` time.
    threshold:
        Success boundary; ``success = score >= threshold``. Default ``0.5`` so VERIFY=1.0
        passes and REFUTE=0.0 fails — the natural split for a 0/1 metric.
    claim_extractor:
        ``(test_case) -> str`` returning the claim to verify. Default
        :func:`default_claim_extractor` (``additional_metadata['claim']`` else
        ``actual_output``).
    abstain_score:
        Score assigned on ABSTAIN. Default ``1.0`` (pass-through: an undecidable claim is
        not a refutation). Set ``0.0`` to fail ABSTAINs.
    strict_abstain:
        Shorthand for ``abstain_score=0.0`` (overrides ``abstain_score``). Default ``False``.
    empty_score:
        Score when the extractor returns no claim (nothing to check). Default ``1.0``.
    allow_llm:
        Forwarded to ``NumProof.verify(allow_llm=...)``. Default ``False`` keeps the metric
        fully deterministic.
    include_reason:
        DeepEval convention; if ``True`` (default) populate ``self.reason``.
    async_mode:
        DeepEval convention honoured by the framework's runner. The verifier call is
        synchronous HTTP, so ``a_measure`` delegates to ``measure``.
    strict_mode:
        DeepEval convention. When ``True``, snap the threshold to ``1.0`` so only a perfect
        score passes (binary metric). Default ``False``.
    """
    cls = _build_metric_class()
    return cls(
        client,
        threshold=threshold,
        claim_extractor=claim_extractor,
        abstain_score=abstain_score,
        strict_abstain=strict_abstain,
        empty_score=empty_score,
        allow_llm=allow_llm,
        include_reason=include_reason,
        async_mode=async_mode,
        strict_mode=strict_mode,
    )
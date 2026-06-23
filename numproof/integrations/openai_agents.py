# SPDX-License-Identifier: MIT
"""NumProof output guardrail for the OpenAI Agents SDK (the ``openai-agents`` package).

Drop-in *output* guardrail that runs the hosted NumProof deterministic verifier over the
numeric/financial claims in an agent's final answer **before that answer is returned**, and
trips the SDK tripwire when a claim is provably false.

Mapping convention (shared across all NumProof framework integrations)::

    VERIFY   -> pass (claim provably holds)
    REFUTE   -> FAIL / block (tripwire fires; the counterexample is surfaced so the agent
                or your error handler can correct the number)
    ABSTAIN  -> configurable; default = pass-through but flag (NumProof could not decide /
                the statement is not a checkable numeric claim — this is *not* a refutation)

How the SDK uses this
---------------------
Attach the returned guardrail to an agent::

    from agents import Agent
    from numproof.integrations.openai_agents import numproof_output_guardrail

    agent = Agent(
        name="Finance assistant",
        instructions="...",
        output_guardrails=[numproof_output_guardrail()],
    )

When the agent finishes, the SDK calls our guardrail with the final ``output``. If any claim
REFUTEs we return ``GuardrailFunctionOutput(tripwire_triggered=True, ...)``; the SDK then
raises ``OutputGuardrailTripwireTriggered`` and halts the run. The offending claim and its
counterexample are available on the raised exception via
``exc.guardrail_result.output.output_info`` (a :class:`NumProofGuardrailInfo`).

This module is dependency-light: it imports ``agents`` lazily (only when you build a
guardrail) and uses the stdlib-only :class:`numproof.NumProof` client for the actual calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from numproof import NumProof

__all__ = [
    "numproof_output_guardrail",
    "NumProofGuardrailInfo",
    "ClaimVerdict",
    "default_claim_extractor",
]

# --- result payloads -----------------------------------------------------------------------


@dataclass
class ClaimVerdict:
    """One claim and what NumProof said about it."""

    claim: str
    verdict: str                      # "VERIFY" | "REFUTE" | "ABSTAIN" (or "ERROR")
    counterexample: Any = None        # populated by NumProof on REFUTE
    detail: Any = None
    certificate: Any = None


@dataclass
class NumProofGuardrailInfo:
    """Carried on ``GuardrailFunctionOutput.output_info`` for every run.

    Inspect this after a tripwire to see exactly which claim failed and why::

        try:
            await Runner.run(agent, prompt)
        except OutputGuardrailTripwireTriggered as exc:
            info = exc.guardrail_result.output.output_info  # -> NumProofGuardrailInfo
            for bad in info.refuted:
                print(bad.claim, "->", bad.counterexample)
    """

    results: list[ClaimVerdict] = field(default_factory=list)
    blocked: bool = False             # True iff the tripwire was tripped
    reason: str = ""                  # human-readable summary

    @property
    def refuted(self) -> list[ClaimVerdict]:
        return [r for r in self.results if r.verdict == "REFUTE"]

    @property
    def abstained(self) -> list[ClaimVerdict]:
        return [r for r in self.results if r.verdict == "ABSTAIN"]


# --- claim extraction ----------------------------------------------------------------------

# A sentence is worth verifying only if it actually contains a digit. NumProof itself decides
# whether a candidate is a *checkable* numeric claim (returning ABSTAIN otherwise), so the
# extractor only needs to be a cheap pre-filter — better to send a few non-claims (cheaply
# ABSTAINed) than to miss a real one.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_HAS_DIGIT = re.compile(r"\d")


def default_claim_extractor(text: str) -> list[str]:
    """Split free text into candidate numeric claims (sentences containing a digit).

    Replace this with your own callable (e.g. one that reads structured fields off a Pydantic
    output) via the ``claim_extractor=`` argument of :func:`numproof_output_guardrail`.
    """
    candidates = (s.strip() for s in _SENTENCE_SPLIT.split(text or ""))
    return [s for s in candidates if s and _HAS_DIGIT.search(s)]


def _coerce_output_to_text(output: Any) -> str:
    """Best-effort flatten of the agent's final output into text we can scan for claims.

    The SDK's ``output`` is the agent's final result and may be a ``str`` or a structured
    object (e.g. a Pydantic model when ``output_type`` is set). We handle the common shapes;
    for anything exotic, pass a custom ``claim_extractor`` that knows your schema.
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    # Pydantic v2 model -> its declared string field(s); fall back to a JSON-ish dump.
    model_dump = getattr(output, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump()
            if isinstance(data, dict):
                return "\n".join(str(v) for v in data.values() if isinstance(v, (str, int, float)))
        except Exception:
            pass
    # A plain ``response``/``text`` attribute is a very common convention.
    for attr in ("response", "text", "answer", "content"):
        val = getattr(output, attr, None)
        if isinstance(val, str):
            return val
    return str(output)


# --- the guardrail factory -----------------------------------------------------------------


def numproof_output_guardrail(
    client: NumProof | None = None,
    *,
    name: str = "numproof_numbers",
    claim_extractor: Callable[[str], Iterable[str]] = default_claim_extractor,
    block_on_abstain: bool = False,
    allow_llm: bool = False,
    fail_closed: bool = False,
):
    """Build an OpenAI Agents SDK *output* guardrail backed by NumProof.

    Parameters
    ----------
    client:
        A :class:`numproof.NumProof` instance. Defaults to ``NumProof.from_env()`` (reads
        ``NUMPROOF_BASE_URL`` / ``NUMPROOF_API_KEY``).
    name:
        Guardrail name shown in SDK traces.
    claim_extractor:
        Callable turning the agent's output text into candidate claim strings. Override to
        pull claims out of a structured/Pydantic output instead of free text.
    block_on_abstain:
        If ``True``, an ABSTAIN also trips the tripwire (strict mode: "if we can't prove it,
        don't ship it"). Default ``False`` — ABSTAIN passes through but is flagged in
        ``output_info``.
    allow_llm:
        Forwarded to ``NumProof.verify(allow_llm=...)`` to let the service normalize fuzzier
        claims. Default ``False`` keeps verification fully deterministic.
    fail_closed:
        Behavior when a NumProof API call raises. ``False`` (default) = fail-open: log the
        error into ``output_info`` and let the output pass (availability over strictness).
        ``True`` = fail-closed: a verifier error trips the tripwire.

    Returns
    -------
    An ``@output_guardrail``-decorated callable ready for ``Agent(output_guardrails=[...])``.
    """
    # Imported lazily so that merely importing this module does not require ``openai-agents``.
    from agents import (  # type: ignore[import-not-found]
        Agent,
        GuardrailFunctionOutput,
        RunContextWrapper,
        output_guardrail,
    )

    np_client = client or NumProof.from_env()

    @output_guardrail(name=name)
    async def _guardrail(
        ctx: RunContextWrapper[Any],
        agent: "Agent[Any]",
        output: Any,
    ) -> "GuardrailFunctionOutput":
        text = _coerce_output_to_text(output)
        claims: Sequence[str] = list(claim_extractor(text))

        info = NumProofGuardrailInfo()
        for claim in claims:
            try:
                r = np_client.verify(claim, allow_llm=allow_llm)
                info.results.append(
                    ClaimVerdict(
                        claim=claim,
                        verdict=str(r.get("verdict", "ABSTAIN")),
                        counterexample=r.get("counterexample"),
                        detail=r.get("detail"),
                        certificate=r.get("certificate"),
                    )
                )
            except Exception as exc:  # API/network error
                info.results.append(ClaimVerdict(claim=claim, verdict="ERROR", detail=str(exc)))

        # Decide the tripwire.
        refuted = info.refuted
        errored = [r for r in info.results if r.verdict == "ERROR"]
        trip = bool(refuted)
        if block_on_abstain and info.abstained:
            trip = True
        if fail_closed and errored:
            trip = True

        info.blocked = trip
        if refuted:
            first = refuted[0]
            info.reason = (
                f"NumProof REFUTED a numeric claim: {first.claim!r} "
                f"(counterexample: {first.counterexample!r})"
            )
        elif trip and errored:
            info.reason = f"NumProof verification error (fail_closed): {errored[0].detail}"
        elif trip and info.abstained:
            info.reason = (
                f"NumProof could not verify (block_on_abstain): {info.abstained[0].claim!r}"
            )
        else:
            info.reason = "all numeric claims verified or non-blocking"

        return GuardrailFunctionOutput(output_info=info, tripwire_triggered=trip)

    return _guardrail

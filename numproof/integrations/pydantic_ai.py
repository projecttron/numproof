"""NumProof guardrail for Pydantic AI — verify the numbers before the agent answers.

Drop-in output validator that runs an agent's textual result through the hosted
NumProof deterministic verifier. If NumProof can prove a numeric/financial claim is
**false** (REFUTE), this raises ``pydantic_ai.ModelRetry`` with the counterexample so
the model self-corrects on the next attempt instead of shipping a wrong number.

Mapping convention (shared by all NumProof integrations):

    VERIFY  -> pass   (claim proven correct)
    REFUTE  -> block  (claim proven false -> ModelRetry with the counterexample)
    ABSTAIN -> configurable; default pass-through (NumProof could not decide, so it is
               NOT a refutation). Optionally raise on abstain via ``block_on_abstain``.

Usage (the common case — decorate your agent)::

    from pydantic_ai import Agent
    from numproof import NumProof
    from numproof.integrations.pydantic_ai import attach_numproof_validator

    agent = Agent("openai:gpt-4o", output_type=str)
    attach_numproof_validator(agent, NumProof.from_env())

    result = agent.run_sync("What gross margin do we get on 1000 revenue, 600 COGS?")
    print(result.output)   # any refuted number was bounced back to the model first

Or build the validator yourself and register it with ``@agent.output_validator``::

    validator = make_numproof_validator(NumProof.from_env())

    @agent.output_validator
    async def check_numbers(output: str) -> str:
        return validator(output)

Design notes
------------
* Dependency-light: only imports ``pydantic_ai`` (for ``ModelRetry``) and ``numproof``.
  The import is done lazily inside the factory so that merely importing
  ``numproof.integrations`` never requires pydantic-ai to be installed.
* Pydantic AI calls output validators on every *partial* output while streaming. We
  honour ``ctx.partial_output`` and skip verification on partials (NumProof is a network
  call; verifying half-built text wastes budget and may produce spurious REFUTEs).
* The validator is a pass-through: on VERIFY / ABSTAIN it returns ``output`` unchanged,
  so it composes cleanly with structured ``output_type`` agents that emit text.

MIT License.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

# We only need the client *type* for hints; importing the package is cheap (stdlib-only).
from ..client import NumProof

__all__ = [
    "NumProofRefuted",
    "make_numproof_validator",
    "attach_numproof_validator",
]


class NumProofRefuted(Exception):
    """Raised when ``raise_on_refute=False`` and you want to inspect a REFUTE yourself.

    Not used on the Pydantic AI happy path (there we raise ``ModelRetry`` instead), but
    handy if you reuse :func:`make_numproof_validator`'s underlying check outside an agent.
    """

    def __init__(self, claim: str, result: dict[str, Any]):
        self.claim = claim
        self.result = result
        super().__init__(self.format_message(claim, result))

    @staticmethod
    def format_message(claim: str, result: dict[str, Any]) -> str:
        cx = result.get("counterexample")
        detail = result.get("detail")
        msg = f"NumProof REFUTED a numeric claim: {claim!r}."
        if cx:
            msg += f" Counterexample: {cx}."
        if detail and detail != cx:
            msg += f" Detail: {detail}."
        return msg


def _default_claim_extractor(output: Any) -> list[str]:
    """Turn an agent output into a list of claim strings to verify.

    The default treats the whole output as a single claim (NumProof's verifier parses
    natural-language numeric/financial statements). Override via ``claim_extractor`` to,
    e.g., split on sentences/lines or pull claims out of a structured object.
    """
    text = output if isinstance(output, str) else str(output)
    text = text.strip()
    return [text] if text else []


def make_numproof_validator(
    client: NumProof,
    *,
    claim_extractor: Callable[[Any], Iterable[str]] = _default_claim_extractor,
    block_on_abstain: bool = False,
    allow_llm: bool = False,
    on_verdict: Optional[Callable[[str, str, dict[str, Any]], None]] = None,
) -> Callable[[Any], Any]:
    """Build a plain ``(output) -> output`` validator that enforces the NumProof gate.

    The returned callable raises :class:`pydantic_ai.ModelRetry` on REFUTE (and on
    ABSTAIN when ``block_on_abstain=True``), so it can be registered directly with
    ``@agent.output_validator``. On VERIFY / ABSTAIN(default) it returns ``output``
    unchanged.

    Parameters
    ----------
    client:
        A configured :class:`numproof.NumProof` client (e.g. ``NumProof.from_env()``).
    claim_extractor:
        Maps the agent output to the claim string(s) to verify. Default: the whole
        output as one claim. Return an empty iterable to skip verification entirely.
    block_on_abstain:
        If True, an ABSTAIN verdict also raises ModelRetry (strict mode: the agent must
        only emit numbers NumProof can positively verify). Default False (pass-through:
        ABSTAIN is not a refutation, so we let it through and merely surface it via
        ``on_verdict``).
    allow_llm:
        Forwarded to ``client.verify(..., allow_llm=...)``. Default False keeps the check
        fully deterministic.
    on_verdict:
        Optional observer ``(verdict, claim, result) -> None`` called for every claim
        (great for logging/metrics). Never affects control flow.
    """
    # Imported lazily so importing this module without pydantic-ai installed still works
    # for non-agent callers; the symbol is only needed when the validator actually fires.
    from pydantic_ai import ModelRetry

    def _verify_claims(output: Any) -> Any:
        for claim in claim_extractor(output):
            claim = (claim or "").strip()
            if not claim:
                continue
            result = client.verify(claim, allow_llm=allow_llm)
            verdict = str(result.get("verdict", "ABSTAIN")).upper()

            if on_verdict is not None:
                on_verdict(verdict, claim, result)

            if verdict == "REFUTE":
                # Block: hand the counterexample back to the model so it can correct.
                # Raising ModelRetry consumes one unit of the run's output-retry budget.
                raise ModelRetry(NumProofRefuted.format_message(claim, result))

            if verdict == "ABSTAIN" and block_on_abstain:
                detail = result.get("detail") or "no decidable numeric claim found"
                raise ModelRetry(
                    f"NumProof could not verify the numbers in {claim!r} "
                    f"({detail}). Rephrase your answer so every numeric claim is "
                    f"explicit and checkable (state the figures and the relation)."
                )
            # VERIFY, or ABSTAIN in pass-through mode: accept the claim.
        return output

    return _verify_claims


def attach_numproof_validator(
    agent: Any,
    client: NumProof,
    *,
    claim_extractor: Callable[[Any], Iterable[str]] = _default_claim_extractor,
    block_on_abstain: bool = False,
    allow_llm: bool = False,
    on_verdict: Optional[Callable[[str, str, dict[str, Any]], None]] = None,
) -> Callable[[Any], Any]:
    """Register a NumProof output validator on a Pydantic AI ``Agent`` in one call.

    Equivalent to::

        @agent.output_validator
        async def _(ctx, output):
            if ctx.partial_output:      # skip mid-stream partials
                return output
            return validator(output)

    Returns the registered validator function (so you can inspect/remove it). ``agent``
    is typed as ``Any`` to avoid importing pydantic-ai at module import time; pass a real
    ``pydantic_ai.Agent``.
    """
    validator = make_numproof_validator(
        client,
        claim_extractor=claim_extractor,
        block_on_abstain=block_on_abstain,
        allow_llm=allow_llm,
        on_verdict=on_verdict,
    )

    # ``ctx`` is the pydantic_ai RunContext. We take it (first arg) only to read
    # ``partial_output`` — Pydantic AI invokes validators on each streamed partial, and
    # NumProof is a network call we should run only on the final, complete output.
    @agent.output_validator
    async def _numproof_output_validator(ctx: Any, output: Any) -> Any:
        if getattr(ctx, "partial_output", False):
            return output  # don't verify half-streamed text; wait for the final output
        return validator(output)

    return _numproof_output_validator

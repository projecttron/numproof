"""NumProof <-> LangChain integration.

Drop-in "verify the numbers before the agent acts/answers" guardrail for LangChain.

This module gives you two things:

1. ``make_numproof_tool()`` -> a ``StructuredTool`` the agent can *call itself*
   to verify a numeric/financial claim mid-reasoning. The tool returns a short,
   model-friendly string ("VERIFY ..." / "REFUTE: ... counterexample: ..." /
   "ABSTAIN ...") so the agent can read the result and correct course.

2. ``make_numproof_checker()`` -> a ``Runnable`` you splice into an LCEL chain
   *after* the model to verify a numeric claim the model just produced. On REFUTE
   it (by default) raises so the bad number never leaves your pipeline, surfacing
   the counterexample for the agent to fix.

Mapping convention (shared across all NumProof integrations):

    VERIFY  -> pass            (the claim holds)
    REFUTE  -> fail / block    (the claim is false; counterexample is surfaced)
    ABSTAIN -> configurable     (not decidable/checkable; default = pass-through but flagged)

Design notes
------------
* No verification logic lives here. We only call the hosted NumProof engine via
  the official ``numproof`` client (stdlib-only HTTP). See ``numproof/client.py``.
* ``langchain_core`` is an *optional* dependency: importing ``numproof`` or
  ``numproof.integrations`` does NOT import LangChain. You only pay for it here.
* Verified against the current (2026) langchain-core API:
    - ``from langchain_core.tools import BaseTool, StructuredTool``
    - ``StructuredTool.from_function(func=, name=, description=, args_schema=, return_direct=)``
    - ``from langchain_core.runnables import Runnable, RunnableLambda``
  All of ``tool``, ``StructuredTool``, ``BaseTool`` are in ``langchain_core.tools.__all__``.

MIT License. Copyright (c) NumProof contributors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from numproof import NumProof

# Type-only imports so static checkers know the return types without forcing
# langchain to be importable at module import time.
if TYPE_CHECKING:  # pragma: no cover
    from langchain_core.runnables import Runnable
    from langchain_core.tools import BaseTool

__all__ = [
    "NumProofRefuted",
    "format_result",
    "make_numproof_tool",
    "make_numproof_checker",
]

# Verdict constants (match the hosted engine's response `verdict` field).
VERIFY = "VERIFY"
REFUTE = "REFUTE"
ABSTAIN = "ABSTAIN"


class NumProofRefuted(ValueError):
    """Raised by the checker when a claim is REFUTED (and ``on_refute='raise'``).

    Carries the structured NumProof result so callers / agent error-handlers can
    read the counterexample programmatically, not just from the message string.
    """

    def __init__(self, claim: str, result: dict[str, Any]):
        self.claim = claim
        self.result = result
        self.counterexample = result.get("counterexample")
        self.detail = result.get("detail")
        super().__init__(
            f"NumProof REFUTED claim {claim!r}: "
            f"{self.detail or 'claim is false'}"
            + (f" (counterexample: {self.counterexample})" if self.counterexample else "")
        )


def _client(client: Optional[NumProof]) -> NumProof:
    """Return the provided client, or build one from environment variables.

    ``NumProof.from_env()`` reads ``NUMPROOF_BASE_URL`` / ``NUMPROOF_API_KEY``.
    """
    return client if client is not None else NumProof.from_env()


def format_result(claim: str, result: dict[str, Any]) -> str:
    """Render a NumProof result dict as a compact, model-readable string.

    The string is what the *agent* sees, so it leads with the verdict and, on
    REFUTE, foregrounds the counterexample the model needs to self-correct.
    """
    verdict = result.get("verdict", ABSTAIN)
    detail = result.get("detail")
    if verdict == VERIFY:
        return f"VERIFY: the claim is correct. claim={claim!r}" + (f" | {detail}" if detail else "")
    if verdict == REFUTE:
        cex = result.get("counterexample")
        msg = f"REFUTE: the claim is FALSE. claim={claim!r}"
        if detail:
            msg += f" | {detail}"
        if cex:
            msg += f" | counterexample: {cex}"
        return msg
    # ABSTAIN or any unknown verdict -> treat as not-checkable.
    return (
        f"ABSTAIN: NumProof could not decide this claim (it is not a checkable "
        f"numeric/financial statement). claim={claim!r}" + (f" | {detail}" if detail else "")
    )


# --------------------------------------------------------------------------- #
# (1) Agent-callable Tool
# --------------------------------------------------------------------------- #

def make_numproof_tool(
    client: Optional[NumProof] = None,
    *,
    name: str = "verify_number",
    description: Optional[str] = None,
    allow_llm: bool = False,
    return_direct: bool = False,
) -> "BaseTool":
    """Build a LangChain ``StructuredTool`` that verifies one numeric claim.

    Give this to an agent (e.g. via ``create_tool_calling_agent`` / LangGraph)
    so it can fact-check its own arithmetic and financial statements *before*
    committing to an answer.

    Parameters
    ----------
    client:
        A ``numproof.NumProof`` instance. If ``None``, one is built from env
        (``NUMPROOF_BASE_URL`` / ``NUMPROOF_API_KEY``) at call time.
    name:
        Tool name exposed to the model. Default ``"verify_number"``.
    description:
        Tool description shown to the model. A sensible default is provided.
    allow_llm:
        Passed through to ``NumProof.verify``. Keep ``False`` for fully
        deterministic verification (recommended for a guardrail).
    return_direct:
        If ``True``, the agent stops and returns the tool output directly once
        this tool is called. Usually ``False`` so the agent can act on the result.

    Returns
    -------
    A ``langchain_core.tools.BaseTool`` (concretely a ``StructuredTool``).
    """
    # Imported lazily so this dependency is only required when you actually
    # build a tool. Gives a clear, actionable error if langchain isn't installed.
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as exc:  # pragma: no cover - exercised only without langchain
        raise ImportError(
            "make_numproof_tool requires langchain-core. Install it with:\n"
            "    pip install langchain-core"
        ) from exc

    np_client = _client(client)

    if description is None:
        description = (
            "Deterministically verify a numeric or financial claim stated in plain "
            "English (arithmetic, percentages, growth rates, margins, ratios, "
            "footing/totals). Call this BEFORE asserting any computed number to the "
            "user. Input is a single self-contained claim string, e.g. "
            "'gross margin is 60% when gross profit is 600 and revenue is 1000'. "
            "Returns VERIFY (correct), REFUTE (false, with a counterexample), or "
            "ABSTAIN (not a checkable numeric claim)."
        )

    def _verify_number(claim: str) -> str:
        """Verify a single numeric/financial claim and return a verdict string.

        Args:
            claim: A self-contained numeric/financial statement to check.
        """
        result = np_client.verify(claim, allow_llm=allow_llm)
        return format_result(claim, result)

    # StructuredTool.from_function infers a single-arg schema (`claim: str`) from
    # the type-hinted signature + docstring. We set name/description explicitly.
    return StructuredTool.from_function(
        func=_verify_number,
        name=name,
        description=description,
        return_direct=return_direct,
    )


# --------------------------------------------------------------------------- #
# (2) Output-checker Runnable
# --------------------------------------------------------------------------- #

def make_numproof_checker(
    client: Optional[NumProof] = None,
    *,
    allow_llm: bool = False,
    on_refute: str = "raise",
    on_abstain: str = "pass",
) -> "Runnable":
    """Build a ``Runnable`` that verifies a numeric claim and gates the pipeline.

    Splice it into an LCEL chain after the step that produces a numeric claim::

        chain = prompt | model | StrOutputParser() | make_numproof_checker()

    The Runnable's input is the **claim string** to check. Behaviour:

    * VERIFY  -> returns the claim unchanged (pass-through).
    * REFUTE  -> ``on_refute``:
        - ``"raise"``  (default): raise :class:`NumProofRefuted` (carries the
          counterexample) so the wrong number can never leave your pipeline.
        - ``"annotate"``: return a string that prepends a "[REFUTED ...]" warning
          to the original claim, leaving correction to a downstream step.
    * ABSTAIN -> ``on_abstain``:
        - ``"pass"`` (default): return the claim unchanged (NumProof made no
          refutation, so we do not block).
        - ``"annotate"``: return the claim prefixed with an "[UNVERIFIED ...]" flag.
        - ``"raise"``: raise :class:`NumProofRefuted` (strict mode; treat
          un-checkable as failure).

    The Runnable composes like any other LCEL step (``invoke``/``ainvoke``/
    ``batch``/``stream``) because it is a ``RunnableLambda``.
    """
    try:
        from langchain_core.runnables import RunnableLambda
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "make_numproof_checker requires langchain-core. Install it with:\n"
            "    pip install langchain-core"
        ) from exc

    if on_refute not in ("raise", "annotate"):
        raise ValueError("on_refute must be 'raise' or 'annotate'")
    if on_abstain not in ("pass", "annotate", "raise"):
        raise ValueError("on_abstain must be 'pass', 'annotate', or 'raise'")

    np_client = _client(client)

    def _check(claim: str) -> str:
        result = np_client.verify(claim, allow_llm=allow_llm)
        verdict = result.get("verdict", ABSTAIN)

        if verdict == VERIFY:
            return claim  # pass-through: the number is good.

        if verdict == REFUTE:
            if on_refute == "raise":
                raise NumProofRefuted(claim, result)
            # annotate: keep going but flag it loudly for a downstream fixer.
            return f"[REFUTED by NumProof -- {format_result(claim, result)}]\n{claim}"

        # ABSTAIN (or unknown): NumProof made no refutation.
        if on_abstain == "raise":
            raise NumProofRefuted(claim, result)
        if on_abstain == "annotate":
            return f"[UNVERIFIED by NumProof -- {format_result(claim, result)}]\n{claim}"
        return claim  # default: pass-through.

    # RunnableLambda turns our callable into a first-class LCEL Runnable.
    return RunnableLambda(_check)

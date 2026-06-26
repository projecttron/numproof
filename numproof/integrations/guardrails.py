# SPDX-License-Identifier: MIT
"""NumProof validator for Guardrails AI (the ``guardrails-ai`` package / Guardrails Hub).

A drop-in **Validator** that runs the hosted NumProof deterministic verifier over a
numeric/financial claim (or over every checkable claim it can extract from a model's text)
and gates the output accordingly:

    VERIFY   -> PassResult        (the claim provably holds)
    REFUTE   -> FailResult        (the claim is provably false; the counterexample is put in
                                   ``error_message``; the validator's ``on_fail`` action then
                                   decides whether to raise / filter / reask / fix)
    ABSTAIN  -> configurable       (NumProof could not decide / it is not a checkable numeric
                                   statement — this is *not* a refutation; default = pass-through)

How Guardrails uses this
------------------------
Attach the validator to a ``Guard`` and let Guardrails drive ``on_fail``::

    from guardrails import Guard
    from numproof.integrations.guardrails import make_numproof_validator

    NumProofClaim = make_numproof_validator(on_abstain=\"pass\")
    guard = Guard().use(NumProofClaim, on_fail=\"exception\")  # any OnFailAction
    guard.validate(\"revenue grew 20% from 500 to 600\")        # -> raises on REFUTE

Or, once published to the Hub, the canonical Hub flow is identical::

    # $ guardrails hub install hub://numproof/num_proof_claim
    from guardrails.hub import NumProofClaim

This module is dependency-light: ``guardrails`` is imported **lazily** (only when the class
is built/used), so importing ``numproof`` or ``numproof.integrations`` never requires
Guardrails to be installed. The actual verification is delegated to the stdlib-only
:class:`numproof.NumProof` HTTP client.

Verified against the current (2026) Guardrails validator API:
    - ``from guardrails.validator_base import Validator, register_validator,
      PassResult, FailResult, ValidationResult``
    - ``@register_validator(name=\"namespace/snake_name\", data_type=\"string\")``
    - ``Validator.__init__(self, on_fail=None, **kwargs)`` (kwargs forwarded to super)
    - user implements ``_validate(self, value, metadata) -> ValidationResult``
    - ``FailResult(error_message=..., fix_value=...)`` / ``PassResult()``
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from numproof import NumProof

# Type-only imports so static checkers know the return types without forcing
# ``guardrails`` to be importable at module-import time.
if TYPE_CHECKING:  # pragma: no cover
    from guardrails.validator_base import (
        FailResult,
        PassResult,
        ValidationResult,
        Validator,
    )

__all__ = [
    "make_numproof_validator",
    "format_refutation",
    "default_claim_extractor",
    "VERIFY",
    "REFUTE",
    "ABSTAIN",
]

# Verdict constants (match the hosted engine's response ``verdict`` field).
VERIFY = "VERIFY"
REFUTE = "REFUTE"
ABSTAIN = "ABSTAIN"

# Hub/registration metadata (used by `guardrails hub submit` and the Hub listing).
VALIDATOR_ID = "numproof/num_proof_claim"

# --- claim extraction ----------------------------------------------------------------------

# A sentence is worth verifying only if it contains a digit. NumProof itself decides whether a
# candidate is a *checkable* numeric claim (returning ABSTAIN otherwise), so the extractor is
# only a cheap pre-filter: better to send a few non-claims (cheaply ABSTAINed) than to miss a
# real one.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_HAS_DIGIT = re.compile(r"\d")


def default_claim_extractor(text: str) -> List[str]:
    """Split free text into candidate numeric claims (sentences containing a digit).

    Override via the ``claim_extractor=`` argument of :func:`make_numproof_validator` to pull
    claims from a structured field instead of free text.
    """
    candidates = (s.strip() for s in _SENTENCE_SPLIT.split(text or ""))
    return [s for s in candidates if s and _HAS_DIGIT.search(s)]


def format_refutation(claim: str, result: Dict[str, Any]) -> str:
    """Build the ``error_message`` Guardrails shows / raises on a REFUTE.

    Leads with the verdict and foregrounds the counterexample so a downstream reask/fix step
    (or the human reading the exception) has exactly what it needs to self-correct.
    """
    detail = result.get("detail")
    cex = result.get("counterexample")
    msg = f"NumProof REFUTED the numeric claim {claim!r}: the statement is FALSE."
    if detail:
        msg += f" {detail}"
    if cex:
        msg += f" Counterexample: {cex}."
    return msg


# --- the validator factory -----------------------------------------------------------------


def make_numproof_validator(
    *,
    client: Optional[NumProof] = None,
    claim_extractor: Optional[Callable[[str], Iterable[str]]] = None,
    on_abstain: str = "pass",
    allow_llm: bool = False,
    fail_closed: bool = False,
    validator_name: str = VALIDATOR_ID,
):
    """Build (and register) the NumProof Guardrails :class:`Validator` class.

    ``guardrails`` is imported here, lazily, so that merely importing this module never
    requires Guardrails to be installed. The returned class is decorated with
    ``@register_validator`` and is ready to pass to ``Guard().use(...)``.

    Parameters
    ----------
    client:
        A :class:`numproof.NumProof` instance baked into the class as the default. If
        ``None``, each instance falls back to ``NumProof.from_env()`` (reads
        ``NUMPROOF_BASE_URL`` / ``NUMPROOF_API_KEY``) — or to a per-instance ``client=``
        passed through ``Guard().use(...)``.
    claim_extractor:
        Callable turning a model's output text into candidate claim strings. If the value
        handed to the validator is already a single self-contained claim, it is verified
        directly; this extractor only runs when the value looks like multi-sentence prose.
        Defaults to :func:`default_claim_extractor`.
    on_abstain:
        What an ABSTAIN means. One of:
            ``"pass"``   (default) — ABSTAIN is not a refutation, so let the value pass.
            ``"fail"``   — strict mode: "if we can't prove it, don't ship it" (FailResult).
            ``"filter"`` — return a FailResult whose fix_value strips the unverifiable claim.
    allow_llm:
        Forwarded to ``NumProof.verify(allow_llm=...)`` to let the service normalize fuzzier
        claims. Default ``False`` keeps verification fully deterministic.
    fail_closed:
        Behavior when a NumProof API call raises. ``False`` (default) = fail-open: a verifier
        error passes the value through (availability over strictness). ``True`` = fail-closed:
        a verifier error produces a FailResult.
    validator_name:
        The Hub-style ``namespace/snake_name`` id used by ``@register_validator(name=...)``.

    Returns
    -------
    A ``@register_validator``-decorated subclass of ``guardrails.validator_base.Validator``,
    ready for ``Guard().use(TheClass, on_fail=...)``.
    """
    # Imported lazily so this dependency is only required when you actually build the
    # validator. Gives a clear, actionable error if guardrails isn't installed.
    try:
        from guardrails.validator_base import (  # type: ignore[import-not-found]
            FailResult,
            PassResult,
            ValidationResult,
            Validator,
            register_validator,
        )
    except ImportError as exc:  # pragma: no cover - exercised only without guardrails
        raise ImportError(
            "The NumProof Guardrails validator requires guardrails-ai. Install it with:\n"
            "    pip install guardrails-ai"
        ) from exc

    if on_abstain not in ("pass", "fail", "filter"):
        raise ValueError("on_abstain must be 'pass', 'fail', or 'filter'")

    default_client = client
    extractor = claim_extractor or default_claim_extractor

    @register_validator(name=validator_name, data_type="string")
    class NumProofClaim(Validator):
        """Deterministically verify numeric/financial claims with the hosted NumProof engine.

        **Key properties**

        - On **REFUTE** -> ``FailResult`` whose ``error_message`` carries the counterexample;
          the configured ``on_fail`` action (exception / reask / fix / filter / noop) decides
          what Guardrails does next.
        - On **VERIFY** -> ``PassResult``.
        - On **ABSTAIN** -> configurable (``on_abstain``); default pass-through, because an
          inability to decide is *not* a refutation.

        **Parameters** (all overridable per-instance via ``Guard().use(...)``)

        - ``client``: a ``numproof.NumProof`` instance (else ``NumProof.from_env()``).
        - ``on_abstain``: ``"pass"`` | ``"fail"`` | ``"filter"``.
        - ``allow_llm``: forward to ``NumProof.verify`` (default deterministic).
        - ``fail_closed``: treat verifier/API errors as failures (default fail-open).
        """

        # Make the configured args round-trip through serialization (Hub/Guard rehydration).
        def __init__(
            self,
            on_fail: Optional[Callable] = None,
            *,
            client: Optional[NumProof] = None,
            on_abstain: str = on_abstain,
            allow_llm: bool = allow_llm,
            fail_closed: bool = fail_closed,
            **kwargs: Any,
        ):
            # Pass the simple, JSON-serializable knobs up to the base class so they are
            # captured for tracing/rehydration; the (non-serializable) client is kept local.
            super().__init__(
                on_fail=on_fail,
                on_abstain=on_abstain,
                allow_llm=allow_llm,
                fail_closed=fail_closed,
                **kwargs,
            )
            if on_abstain not in ("pass", "fail", "filter"):
                raise ValueError("on_abstain must be 'pass', 'fail', or 'filter'")
            self._client = client or default_client or NumProof.from_env()
            self._on_abstain = on_abstain
            self._allow_llm = allow_llm
            self._fail_closed = fail_closed

        def _verify_one(self, claim: str) -> Dict[str, Any]:
            return self._client.verify(claim, allow_llm=self._allow_llm)

        def _validate(
            self, value: Any, metadata: Optional[Dict[str, Any]] = None
        ) -> "ValidationResult":
            text = value if isinstance(value, str) else str(value)
            metadata = metadata or {}

            # If the value is a single sentence/claim, verify it directly; otherwise extract
            # the checkable claims from the prose. A per-call ``claim_extractor`` in metadata
            # wins, then the factory-level extractor.
            meta_extractor = metadata.get("claim_extractor")
            if callable(meta_extractor):
                claims: List[str] = list(meta_extractor(text))
            elif _SENTENCE_SPLIT.search(text):
                claims = list(extractor(text))
            else:
                claims = [text.strip()] if text.strip() else []

            if not claims:
                # Nothing checkable in the value -> treat like ABSTAIN.
                return self._abstain_result(value, claim="(no checkable numeric claim)")

            abstained: List[str] = []
            for claim in claims:
                try:
                    result = self._verify_one(claim)
                except Exception as exc:  # API / network error
                    if self._fail_closed:
                        return FailResult(
                            error_message=(
                                f"NumProof verification error for claim {claim!r} "
                                f"(fail_closed): {exc}"
                            )
                        )
                    continue  # fail-open: ignore this claim

                verdict = str(result.get("verdict", ABSTAIN)).upper()
                if verdict == REFUTE:
                    # The first provable falsehood gates the whole value. ``fix_value=""``
                    # lets an ``on_fail="fix"/"filter"`` action drop the bad text.
                    return FailResult(
                        error_message=format_refutation(claim, result),
                        fix_value="",
                    )
                if verdict == VERIFY:
                    continue
                abstained.append(claim)  # ABSTAIN / unknown

            # No REFUTE. If everything we could check VERIFIED, pass.
            if not abstained:
                return PassResult()

            # Some claims only ABSTAINed -> apply the configured policy.
            return self._abstain_result(value, claim=abstained[0])

        def _abstain_result(self, value: Any, *, claim: str) -> "ValidationResult":
            if self._on_abstain == "pass":
                return PassResult()
            message = (
                f"NumProof could not verify the numeric claim {claim!r} "
                f"(ABSTAIN): it is not a decidable numeric/financial statement."
            )
            if self._on_abstain == "filter":
                # Drop the unverifiable content; on_fail='fix'/'filter' uses fix_value.
                return FailResult(error_message=message, fix_value="")
            # on_abstain == "fail"
            return FailResult(error_message=message)

    return NumProofClaim
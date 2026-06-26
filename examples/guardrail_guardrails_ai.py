"""Example: NumProof Guardrails AI validator catching a wrong number.

Run:
    pip install "guardrails-ai>=0.5" numproof
    export NUMPROOF_API_KEY=...     # or use the free /demo base_url
    python guardrail_guardrails_ai.py

Shows the NumProof Validator wired into a Guardrails ``Guard``:
  - VERIFY  -> passes
  - REFUTE  -> FailResult; on_fail="exception" raises with the counterexample
  - ABSTAIN -> configurable (here: pass-through)

This uses the local factory entry point. Once the validator is published to the
Guardrails Hub the import becomes ``from guardrails.hub import NumProofClaim`` after
``guardrails hub install hub://numproof/num_proof_claim`` — everything else is identical.
"""

from guardrails import Guard
from guardrails.errors import ValidationError

from numproof import NumProof
from numproof.integrations.guardrails import make_numproof_validator

# Build the validator class (lazy-imports guardrails internally) sharing one NumProof client.
# NumProof.from_env() reads NUMPROOF_BASE_URL / NUMPROOF_API_KEY.
client = NumProof.from_env()
NumProofClaim = make_numproof_validator(client=client, on_abstain="pass")

# Attach it to a Guard. on_fail="exception" -> a REFUTE raises ValidationError.
guard = Guard().use(NumProofClaim, on_fail="exception")

# --- (1) a correct claim passes -------------------------------------------- #
ok = guard.validate("gross margin is 60% when gross profit is 600 and revenue is 1000")
print("PASS ->", ok.validation_passed, "|", ok.validated_output)

# --- (2) a wrong claim is BLOCKED with the counterexample ------------------ #
try:
    guard.validate("operating cash flow grew 20% from 1000 to 1180")
except ValidationError as e:
    print("BLOCKED ->", e)   # message carries: REFUTED ... Counterexample: actual growth +18%

# --- (3) ABSTAIN policy is configurable ------------------------------------ #
# Strict mode: treat "can't prove it" as a failure too.
strict_cls = make_numproof_validator(client=client, on_abstain="fail")
strict_guard = Guard().use(strict_cls, on_fail="exception")
try:
    strict_guard.validate("the meeting is scheduled for 3pm on the 4th floor")
except ValidationError as e:
    print("ABSTAIN(strict) BLOCKED ->", e)

# --- (4) extracting claims from free-form model prose ---------------------- #
# When the value is multi-sentence text, the validator pre-filters sentences
# containing a digit and verifies each; the first REFUTE gates the output.
report = (
    "Q3 revenue was 1200 and Q2 was 1000. "
    "That represents 30% growth quarter over quarter."  # actually +20% -> REFUTE
)
try:
    guard.validate(report)
except ValidationError as e:
    print("REPORT BLOCKED ->", e)
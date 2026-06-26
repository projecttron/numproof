"""Example: scoring LLM outputs with the NumProof DeepEval metric.

NumProofMetric is a DETERMINISTIC DeepEval metric: instead of asking a judge LLM whether
the answer's numbers look right, it asks the NumProof engine for a proof or a
counterexample. VERIFY -> score 1.0 (pass), REFUTE -> score 0.0 (fail, with the
counterexample on .reason), ABSTAIN -> configurable (default pass-through).

Run:
    pip install deepeval numproof          # or: pip install 'numproof[deepeval]'
    export NUMPROOF_API_KEY=...            # or point NUMPROOF_BASE_URL at the /demo service
    python deepeval_metric.py

Optional — stream results to the Confident AI dashboard:
    deepeval login                         # opens browser, pastes your API key
    # then re-run; evaluate() below will publish a test run to the platform.
"""

from deepeval.test_case import LLMTestCase

from numproof import NumProof
from numproof.integrations.deepeval import NumProofMetric

# One shared client. NumProof.from_env() reads NUMPROOF_BASE_URL / NUMPROOF_API_KEY.
client = NumProof.from_env()

# threshold=0.5 -> VERIFY (1.0) passes, REFUTE (0.0) fails.
metric = NumProofMetric(client, threshold=0.5)
print("Metric name:", metric.__name__)

# --- (1) A correct answer -> VERIFY -> score 1.0, success=True -------------------------- #
good = LLMTestCase(
    input="What is 120 + 90 + 340 + 15?",
    actual_output="The total is 565.",
)
metric.measure(good)
print("\n[correct]   score:", metric.score, "success:", metric.is_successful())
print("            reason:", metric.reason)

# --- (2) A WRONG answer -> REFUTE -> score 0.0, success=False, counterexample on reason - #
bad = LLMTestCase(
    input="Operating cash flow went from 1000 to 1180. What was the growth?",
    actual_output="Operating cash flow grew 20% from 1000 to 1180.",
)
metric.measure(bad)
print("\n[wrong]     score:", metric.score, "success:", metric.is_successful())
print("            reason:", metric.reason)   # -> ... actual growth is +18% ...

# --- (3) Pin the exact claim via additional_metadata ----------------------------------- #
# When the answer is prose, store the precise statement to check in additional_metadata;
# default_claim_extractor prefers it over actual_output.
pinned = LLMTestCase(
    input="Compute the gross margin.",
    actual_output="With $1,000 revenue and $400 COGS, the business is healthy.",
    additional_metadata={"claim": "gross margin is 60% when revenue is 1000 and COGS is 400"},
)
metric.measure(pinned)
print("\n[pinned]    score:", metric.score, "success:", metric.is_successful())
print("            reason:", metric.reason)

# --- (4) Run inside DeepEval's evaluate() (and the Confident AI dashboard if logged in) - #
# A fresh metric instance per run is the DeepEval convention.
from deepeval import evaluate  # noqa: E402

evaluate(test_cases=[good, bad, pinned], metrics=[NumProofMetric(client, threshold=0.5)])

# --- (5) pytest-style assertion (put this in a test_*.py and run `deepeval test run`) --- #
#
#     from deepeval import assert_test
#     def test_no_wrong_numbers():
#         tc = LLMTestCase(input="2+2?", actual_output="2 + 2 = 4")
#         assert_test(tc, [NumProofMetric(threshold=0.5)])
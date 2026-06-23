"""Example: NumProof LangChain guardrail catching a wrong number.

Run:
    pip install langchain-core numproof
    export NUMPROOF_API_KEY=...     # or use the free /demo base_url
    python langchain_guardrail_example.py

Shows BOTH integration entry points:
  (1) the agent-callable Tool, and
  (2) the output-checker Runnable that BLOCKS a false claim with a counterexample.
"""

from numproof import NumProof
from numproof.integrations.langchain import (
    make_numproof_tool,
    make_numproof_checker,
    NumProofRefuted,
)

# Build one client and share it across both helpers.
# NumProof.from_env() reads NUMPROOF_BASE_URL / NUMPROOF_API_KEY.
client = NumProof.from_env()

# --- (1) Agent-callable Tool ------------------------------------------------ #
# Hand this to a tool-calling agent (create_tool_calling_agent / LangGraph) so it
# can verify its own numbers before answering.
verify_tool = make_numproof_tool(client)
print("Tool name:", verify_tool.name)

# A correct claim -> VERIFY
print(verify_tool.invoke({"claim": "gross margin is 60% when gross profit is 600 and revenue is 1000"}))

# A WRONG claim -> the tool surfaces REFUTE + counterexample for the agent to fix.
print(verify_tool.invoke({"claim": "operating cash flow grew 20% from 1000 to 1180"}))
#   -> "REFUTE: the claim is FALSE. ... | counterexample: ... (actual growth is +18%)"

# --- (2) Output-checker Runnable (the guardrail) ---------------------------- #
# Drop this in an LCEL chain after the model:
#     chain = prompt | model | StrOutputParser() | checker
# Here we invoke it directly on a claim a model might have produced.
checker = make_numproof_checker(client, on_refute="raise")

# Correct number passes straight through.
good = checker.invoke("two 10% raises equal a 21% total increase")
print("PASS ->", good)

# Wrong number is BLOCKED before it can leave the pipeline.
try:
    checker.invoke("operating cash flow grew 20% from 1000 to 1180")
except NumProofRefuted as e:
    print("BLOCKED ->", e)                 # human-readable message
    print("  counterexample:", e.counterexample)  # structured, for programmatic fixing

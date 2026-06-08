"""The agent gate: an AI agent must NOT ship a numeric claim NumProof can't VERIFY.

Drop this check between your model and your user/report/tool. If the number isn't provably
right, the answer is flagged unsupported instead of silently wrong.

Run: python examples/agent_gate.py  (needs NUMPROOF_API_KEY)
"""
from numproof import NumProof

np = NumProof.from_env()


def gate(agent_claim: str) -> str:
    r = np.verify(agent_claim)
    if r["verdict"] == "VERIFY":
        return f"✅ sent: {agent_claim}"
    if r["verdict"] == "REFUTE":
        return f"🛑 blocked (false): {agent_claim}\n   → {r.get('counterexample')}"
    return f"⚠️  flagged unsupported (NumProof abstained): {agent_claim}"


# imagine these came out of your LLM agent
for claim in [
    "revenue of 1200 + 450 + 80 totals 1730",     # true  -> sent
    "a 40% gross margin on 1000 revenue is 600",   # false -> blocked (margin is 400)
    "the market will grow 20% next year",          # not checkable -> flagged
]:
    print(gate(claim))

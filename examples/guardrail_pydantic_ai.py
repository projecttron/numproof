"""NumProof guardrail for a Pydantic AI agent — catch a wrong number before it ships.

The validator runs the agent's answer through NumProof. On a REFUTE it raises ModelRetry
with the counterexample, so the model gets a second shot and self-corrects.

Run (real):   pip install pydantic-ai && export NUMPROOF_API_KEY=...  # plus your model key
              python examples/pydantic_ai_guardrail.py
Run (demo):   python examples/pydantic_ai_guardrail.py --demo   # no network, no LLM needed
"""
from __future__ import annotations

import sys

from numproof import NumProof
from numproof.integrations.pydantic_ai import attach_numproof_validator


def real() -> None:
    """Wire the guardrail onto a live Pydantic AI agent."""
    from pydantic_ai import Agent

    agent = Agent(
        "openai:gpt-4o",
        output_type=str,
        # Give the model a retry budget so ModelRetry has somewhere to go.
        retries={"output": 2},
        system_prompt="You are a finance assistant. State numeric results explicitly.",
    )

    # One line attaches the "verify the numbers before you answer" gate.
    attach_numproof_validator(
        agent,
        NumProof.from_env(),
        on_verdict=lambda v, claim, _r: print(f"[numproof] {v}: {claim}"),
    )

    result = agent.run_sync("What gross margin do we get on 1000 revenue with 600 COGS?")
    print("\nFinal answer:", result.output)


def demo() -> None:
    """Offline demo: stub ModelRetry + a fake NumProof transport prove the REFUTE bounce."""
    import json
    import types

    # Stand in for pydantic_ai so the demo runs without the package installed.
    pa = types.ModuleType("pydantic_ai")

    class ModelRetry(Exception):
        ...

    pa.ModelRetry = ModelRetry
    sys.modules.setdefault("pydantic_ai", pa)

    from numproof.integrations.pydantic_ai import make_numproof_validator

    def fake_transport(method, url, headers, data, timeout):
        # Stand-in for the hosted engine: 40% of 1000 is 400, so "...is 600" is false.
        claim = json.loads(data)["claim"]
        if "is 600" in claim:  # the wrong number
            body = {
                "verdict": "REFUTE",
                "counterexample": "40% of 1000 = 400, not 600",
                "detail": "gross margin = gross_profit / revenue",
            }
        else:  # "...is 400" -> correct
            body = {"verdict": "VERIFY", "certificate": "cert-demo"}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()

    client = NumProof(base_url="https://numproof.com", transport=fake_transport)
    validate = make_numproof_validator(client)

    # Simulate two model attempts: first wrong, then corrected.
    attempts = [
        "a 40% gross margin on 1000 revenue is 600",   # WRONG -> blocked
        "a 40% gross margin on 1000 revenue is 400",   # CORRECT -> passes
    ]
    for i, answer in enumerate(attempts, 1):
        try:
            ok = validate(answer)
            print(f"attempt {i}: PASS -> {ok}")
            break
        except pa.ModelRetry as e:
            # In a real run, Pydantic AI feeds this message back to the model.
            print(f"attempt {i}: BLOCKED -> {e}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        real()

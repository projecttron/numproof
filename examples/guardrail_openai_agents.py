# SPDX-License-Identifier: MIT
"""Demo: a NumProof output guardrail catching a WRONG number in an agent's answer.

Run:
    pip install openai-agents numproof
    export OPENAI_API_KEY=...          # for the agent's LLM
    export NUMPROOF_API_KEY=...        # for the hosted verifier
    python examples/openai_agents_guardrail.py

The agent is *instructed to state a false margin*. NumProof REFUTEs the claim, the SDK
tripwire fires, and we catch ``OutputGuardrailTripwireTriggered`` and print the
counterexample instead of shipping the bad number.
"""
import asyncio

from agents import Agent, Runner, OutputGuardrailTripwireTriggered

from numproof.integrations.openai_agents import numproof_output_guardrail


async def main() -> None:
    agent = Agent(
        name="Finance assistant",
        instructions=(
            "You report financial figures. When asked, answer in one short sentence "
            "stating the gross margin as a percentage."
        ),
        # NumProof.from_env() is used by default; pass a configured client if you prefer.
        output_guardrails=[numproof_output_guardrail()],
    )

    # This prompt steers the model into a provably false statement.
    prompt = (
        "Revenue is 1000 and gross profit is 600. "
        "State (incorrectly, for this test) that the gross margin is 40%."
    )

    try:
        result = await Runner.run(agent, prompt)
        # If we get here the numbers passed verification.
        print("PASSED guardrail, agent said:", result.final_output)
    except OutputGuardrailTripwireTriggered as exc:
        info = exc.guardrail_result.output.output_info  # NumProofGuardrailInfo
        print("BLOCKED by NumProof output guardrail.")
        print("  reason:", info.reason)
        for bad in info.refuted:
            print(f"  REFUTED: {bad.claim!r}")
            print(f"           counterexample: {bad.counterexample!r}")
        # 40% of 1000 is 400, not 600 -> margin is 60%, so "40%" is REFUTED.


if __name__ == "__main__":
    asyncio.run(main())

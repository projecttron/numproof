# How NumProof compares

NumProof occupies a gap nobody else fills cleanly: a **self-serve, spreadsheet-native,
deterministic numeric verifier with an API/CLI/MCP/x402 motion.**

| | **NumProof** | LLM guardrails (Patronus, Guardrails AI, Lakera) | AWS Automated Reasoning | Wolfram / SymPy | Finance suites (Trullion, BlackLine) |
|---|---|---|---|---|---|
| Deterministic numeric/financial verdict | ✅ exact arithmetic + symbolic | ❌ model judges model | ✅ formal, but policy-centric | ✅ compute engine | ◻ workflow, not a verdict API |
| Cell/formula provenance on `xlsx/csv` | ✅ | ❌ text-first | ❌ not spreadsheet-native | ❌ | ✅ |
| Self-serve API / CLI / MCP | ✅ | ◻ partial | ◻ inside Bedrock | ◻ API, not workflow | ❌ demo-led sales |
| Signed, offline-verifiable evidence | ✅ `/audit/verify` | ❌ | ◻ | ❌ | ◻ internal audit trail |
| Covenant / ratio / footing packs | ✅ | ❌ | ❌ | ❌ | ✅ |
| Pay-per-call (x402) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Free self-serve start | ✅ | ◻ | ❌ | ◻ non-commercial | ❌ |

**The two market gaps NumProof sits between:**

1. *Guardrails for text* catch hallucinations and prompt injection but rarely live in a real
   `xlsx/csv` context or give cell-level, formula-level evidence.
2. *Finance suites* do validation and traceability but sell as heavy, demo-led suites — not a
   lightweight self-serve API/CLI/MCP building block.

NumProof is the productized transition layer: **upload/POST → verdict with provenance → signed
audit package → call it from an agent via MCP/API/x402.** Formal (Lean) proofs are available as a
premium evidence artifact, not a required interface.

*Positioning, not a benchmark — verify against your own workflow at https://numproof.com.*

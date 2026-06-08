# NumProof

**The deterministic numeric truth layer for AI agents and spreadsheets.**

Your agent writes *"gross margin improved from 42.1% to 44.8%"* or *"the workbook is internally
consistent"* — NumProof tells you, deterministically, whether that number is **VERIFY**, **REFUTE**,
or **ABSTAIN**, with a counterexample, cell/formula provenance, and a signed, machine-checkable
audit bundle. It's exact arithmetic and symbolic math — **not an LLM judging another LLM.**

- ✅ Verify a single math/finance claim, or batch thousands in CI
- ✅ Audit `xlsx/csv` rows: footing, cross-footing, balance-sheet ties, margins, formula cells — with provenance
- ✅ Diff two report versions; evaluate covenant rule packs (DSCR, Debt/EBITDA, current ratio, …)
- ✅ Signed evidence bundle (JSON + HTML/PDF/ZIP) anyone can re-verify offline
- ✅ API · CLI · **MCP server** · optional **x402** pay-per-call

> This repo is the **open-source client** (SDK + MCP). The verification engine runs as a hosted
> service — `pip install numproof`, point it at the API, done. (Same shape as `stripe-python`:
> the SDK is open, the engine is the service.)

Live demo (no key): **https://numproof.com** · Docs: **https://numproof.com/docs**

---

## 30-second start

```bash
pip install numproof
```

```python
from numproof import NumProof

np = NumProof.from_env()              # NUMPROOF_API_KEY (get a free key: see below)

print(np.verify("120 + 90 + 340 + 15 == 565"))
# {'verdict': 'VERIFY', 'certificate': 'EXACT_ARITHMETIC', ...}

print(np.verify("a 50% loss needs a 100% gain to break even"))   # VERIFY: (1-0.5)*(1+1.0)==1
print(np.verify("two 10% raises equal a 21% total increase"))    # VERIFY
print(np.verify("operating margin is 18% when EBIT is 180 and revenue is 1000"))  # VERIFY
```

No install? It's just HTTP:

```bash
curl -s https://numproof.com/demo -H 'Content-Type: application/json' \
  -d '{"claim":"gross margin is 60% when gross profit is 600 and revenue is 1000"}'
```

Free API key:

```bash
curl -s https://numproof.com/signup -X POST -H 'Content-Type: application/json' -d '{}'
```

---

## Audit a spreadsheet (with provenance)

```python
rows = [["Revenue", 1000], ["COGS", 400], ["Gross Profit", 600], ["Gross Margin", "60%"]]
print(np.audit_rows(rows)["verdict"])          # PASS  (600/1000 == 60%, footing, ties, ...)

# covenant rule packs: DSCR, Debt/EBITDA, current ratio, custom thresholds
print(np.covenant_rows(
    [["EBITDA", 500], ["Debt Service", 300], ["Debt", 1200]],
    rule_pack="credit_covenants_basic",
)["verdict"])
```

Every audit/diff/covenant result can be returned as a **signed bundle** + human-readable HTML/PDF
report (`format="zip"`). Recipients verify it **without trusting you or NumProof**:

```bash
curl -s https://numproof.com/audit/verify -H 'Content-Type: application/json' -d @bundle.json
# {"valid": true, "verdict": "PASS", "signer": "0x...", ...}
```

---

## Use it from an AI agent (MCP)

NumProof ships an MCP server so Claude / OpenAI-style agents can call it as a tool — gate every
numeric claim before it reaches a user, report, or auditor.

```bash
python -m numproof.mcp
```

```json
{ "mcpServers": { "numproof": { "command": "python", "args": ["-m", "numproof.mcp"] } } }
```

Or point any MCP client at the hosted descriptor: `https://numproof.com/mcp.json`.

See [`examples/`](examples/) for runnable scripts (verify, audit, covenants, agent-gate, MCP).

---

## Why deterministic (and why it matters)

Generic "AI guardrails" use a model to grade a model — probabilistic, and itself can hallucinate.
NumProof recomputes the math **exactly** (rational arithmetic + symbolic identity checking) and
returns a reproducible verdict with a trace. When it can't prove something it says **ABSTAIN**
rather than guess. For finance, regulated, and agent workflows, *"the number is provably right"*
beats *"another model thinks it looks right."* Full table: [`comparison.md`](comparison.md).

---

## Pricing

| Plan | Price | For |
|---|---|---|
| Sandbox | **$0** | web demo + free credits |
| x402 PAYG | **$0.025 / credit** | agent-to-tool, no subscription |
| Builder | **$29/mo** | API + MCP + CLI, 2k credits |
| Pro | **$99/mo** | batch, webhooks, CI, signed exports, 10k credits |
| Finance Team | **$299/mo** | 5 seats, version diff, covenant packs, branded exports |

---

## What's in this repo

The `numproof` Python SDK (`NumProof` client), the MCP server, and runnable examples — all thin
HTTP clients to the hosted API. **MIT licensed.** The verification engine, finance audit logic,
formal (Lean) proof tier, and signing are the hosted service and are **not** in this repo.

Found a wrong verdict? Open an issue with the exact claim — correctness is the whole product.

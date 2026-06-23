# NumProof Verification Receipt — format & offline re-checking

A **Verification Receipt** is a canonical, signed JSON document that lets a *second party* trust
a NumProof verdict **without trusting the transport or re-running the original work** — and, for
exactly-checkable claims, **re-derive the verdict themselves offline** with commodity libraries.

> The asymmetry that makes this a product: *producing* the verdict + signed attestation is the
> hosted NumProof engine (closed). *Checking* a receipt is open, commodity, and in this repo —
> an AI agent can recompute a number for itself, but it cannot issue a signed attestation that an
> independent, named, track-recorded attestor stands behind. That independence is the value.

## Re-check one in 10 seconds

```bash
pip install "numproof[verify]"
numproof-verify receipt.json --signer 0x<published-NumProof-signer>
```

It (1) recomputes the canonical payload hash and recovers the EIP-191 signer → proves the receipt
is untampered and signed by NumProof; (2) for `value` / `agg` / `identity` / `sequence` claims,
independently re-derives the verdict via stdlib `Fraction` + `sympy` and checks it matches. A
tampered field, a wrong signer, or a verdict that doesn't actually hold all fail loudly.

## Structure

```jsonc
{
  "schema": "numproof-verification-receipt-v1",
  "engine": "edra-verify-core",
  "claim": "120 + 90 + 340 + 15 == 565",
  "verdict": "VERIFY",                      // VERIFY | REFUTE | ABSTAIN
  "certificate": "EXACT_ARITHMETIC",
  "detail": "120+90+340+15 = 565 exactly",
  "recheck": {                              // engine-free data to re-derive the verdict offline
    "kind": "value",                        // value | agg | identity | sequence | lean
    "expr": "120+90+340+15",
    "claimed": "565",
    "rederivable": true                     // false for Lean proofs (attestation-only)
  },
  "verified_at": "2026-06-08T...Z",
  "signature": {
    "algorithm": "ETH-EIP191-SECP256K1",
    "key_id": "audit-main-v1",
    "signed_at": "2026-06-08T...Z",
    "payload_sha256": "<sha256 of canonical payload: receipt minus signature and minus _-prefixed keys>",
    "signer": "0x...",                      // recovered must equal this
    "message": "numproof verification receipt\nversion:...\npayload_sha256:...",
    "value": "0x<eip-191 signature>"
  }
}
```

**`recheck` by claim class** (only the caller's own claim — never engine internals):
- `value` → `expr`, `claimed` (re-derive: exact `Fraction(expr) == claimed`)
- `agg` → `values`, `claimed` (re-derive: `sum == claimed`)
- `identity` → `lhs`, `rhs`, `vars` (re-derive: `simplify(lhs-rhs) == 0`)
- `sequence` → `formula`, `target_n`, `claimed` (re-derive: `formula(n=target_n) == claimed`)
- `lean` → `rederivable: false` — **attestation-only**: the signature proves an independent named
  attestor stands behind the Lean-proved verdict; re-deriving it needs a Lean toolchain.

## Trust levels `numproof-verify` reports
- **"independently re-derived + signature valid"** — strongest: you trusted nothing, you checked the math.
- **"signature valid (attestation only)"** — you trust NumProof's signed assertion (Lean class), not re-derived.
- **"SIGNATURE VALID BUT VERDICT MISMATCH" / "INVALID"** — reject: tampered, wrong signer, or the verdict doesn't hold.

## Canonicalization (for independent implementations)
`payload_sha256 = sha256( json.dumps(payload, sort_keys=True, ensure_ascii=False,
separators=(",",":"), default=str) )`, where `payload` is the receipt **minus the `signature`
block and minus any top-level key beginning with `_`**. Keys like `_note` are non-canonical human
annotations excluded from the signature, so a receipt can carry a readable note without invalidating
it. The signed message is the multiline `signature.message` string; recover the address from it via
EIP-191 (`personal_sign`). See `numproof/recheck.py` (MIT).

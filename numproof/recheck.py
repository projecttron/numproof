"""numproof-verify — offline re-checker for NumProof Verification Receipts.

Given a signed receipt, this:
  1. recomputes the canonical payload hash and recovers the signer from the EIP-191 signature
     -> proves the receipt is untampered and was signed by NumProof's published key;
  2. for exactly-checkable claim classes (value / agg / identity / sequence), INDEPENDENTLY
     re-derives the verdict using only commodity math (stdlib Fraction + sympy) -> you trust
     neither the transport nor NumProof; you check it yourself.

Lean-proof receipts are attestation-only (re-deriving needs Lean): the signature still proves
an independent, named attestor stands behind the verdict — which a self-recompute cannot give.

Requires the `verify` extra:  pip install "numproof[verify]"
Run:  numproof-verify receipt.json [--signer 0xPUBLISHED_NUMPROOF_ADDRESS]
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from fractions import Fraction

RECEIPT_ALGORITHM = "ETH-EIP191-SECP256K1"
_MAX_BITS = 64_000  # DoS guard mirrors the service; checking is cheap, keep it cheap


def _canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode("utf-8")


def _payload_hash(receipt: dict) -> str:
    import hashlib
    payload = {k: v for k, v in receipt.items() if k != "signature"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


# --- commodity exact evaluator (NOT the discovery engine; checking exact arithmetic is trivial) ---
def _ev(node, env):
    if isinstance(node, ast.Expression):
        return _ev(node.body, env)
    if isinstance(node, ast.BinOp):
        a, b = _ev(node.left, env), _ev(node.right, env)
        if isinstance(node.op, ast.Add): r = a + b
        elif isinstance(node.op, ast.Sub): r = a - b
        elif isinstance(node.op, ast.Mult):
            if a.numerator.bit_length() + b.numerator.bit_length() > _MAX_BITS:
                raise ValueError("too large")
            r = a * b
        elif isinstance(node.op, ast.Div): r = a / b
        elif isinstance(node.op, ast.Pow):
            if b.denominator != 1 or b < 0 or b > 4096:
                raise ValueError("bad exponent")
            if a.numerator.bit_length() * int(b) > _MAX_BITS:
                raise ValueError("too large")
            r = a ** int(b)
        else:
            raise ValueError("bad op")
        if r.numerator.bit_length() + r.denominator.bit_length() > _MAX_BITS:
            raise ValueError("too large")
        return r
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Constant):
        return Fraction(str(node.value))
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise KeyError(node.id)
        return env[node.id]
    raise ValueError("unsupported")


def _evalf(expr: str, env=None) -> Fraction:
    return _ev(ast.parse(str(expr), mode="eval"), env or {})


def _rederive(rc: dict):
    """Return ('VERIFY'|'REFUTE', detail) re-derived from the recheck block, or (None, reason)."""
    kind = rc.get("kind")
    try:
        if kind == "value":
            ok = _evalf(rc["expr"]) == Fraction(str(rc["claimed"]))
            return ("VERIFY" if ok else "REFUTE", f"{rc['expr']} vs {rc['claimed']}")
        if kind == "agg":
            total = sum((Fraction(str(v)) for v in rc["values"]), Fraction(0))
            ok = total == Fraction(str(rc["claimed"]))
            return ("VERIFY" if ok else "REFUTE", f"sum == {rc['claimed']}")
        if kind == "sequence":
            if not rc.get("formula"):
                return (None, "no formula in receipt (attestation-only)")
            val = _evalf(rc["formula"], {"n": Fraction(int(rc["target_n"]))})
            ok = val == Fraction(str(rc["claimed"]))
            return ("VERIFY" if ok else "REFUTE", f"a({rc['target_n']}) == {rc['claimed']}")
        if kind == "identity":
            import sympy
            syms = {v: sympy.Symbol(v) for v in (rc.get("vars") or [])}
            diff = sympy.simplify(sympy.sympify(rc["lhs"], locals=syms) - sympy.sympify(rc["rhs"], locals=syms))
            return ("VERIFY" if diff == 0 else "REFUTE", f"simplify(lhs-rhs)={diff}")
        return (None, f"claim class '{kind}' not offline-rederivable")
    except Exception as e:
        return (None, f"re-derivation error: {type(e).__name__}: {e}")


def recheck(receipt: dict, expected_signer: str | None = None) -> dict:
    out: dict = {"signature_valid": False, "tamper_check": "FAILED", "trust": "INVALID", "messages": []}
    sig = receipt.get("signature")
    if not isinstance(sig, dict):
        out["messages"].append("no signature block")
        return out
    if sig.get("algorithm") != RECEIPT_ALGORITHM:
        out["messages"].append(f"unsupported algorithm: {sig.get('algorithm')}")
        return out
    # tamper check: recompute payload hash
    recomputed = _payload_hash(receipt)
    out["tamper_check"] = "ok" if recomputed == sig.get("payload_sha256") else "FAILED"
    if out["tamper_check"] != "ok":
        out["messages"].append("payload hash mismatch — receipt was modified after signing")
        return out
    # recover signer
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text=sig["message"]), signature=str(sig["value"]))
    except Exception as e:
        out["messages"].append(f"signature recovery failed: {type(e).__name__}")
        return out
    out["signer"] = recovered
    out["signature_valid"] = recovered.lower() == str(sig.get("signer", "")).lower()
    if not out["signature_valid"]:
        out["messages"].append("recovered signer != claimed signer")
        return out
    if expected_signer:
        out["expected_signer_match"] = recovered.lower() == expected_signer.lower()
        if not out["expected_signer_match"]:
            out["messages"].append(f"signer {recovered} != expected {expected_signer}")
            return out
    # independent re-derivation
    rc = receipt.get("recheck") or {}
    if rc.get("rederivable"):
        verdict, detail = _rederive(rc)
        out["rederived_verdict"] = verdict
        out["rederive_detail"] = detail
        if verdict is not None:
            out["rederive_matches"] = (verdict == receipt.get("verdict"))
            out["trust"] = ("independently re-derived + signature valid"
                            if out["rederive_matches"] else "SIGNATURE VALID BUT VERDICT MISMATCH")
            if not out["rederive_matches"]:
                out["messages"].append(
                    f"re-derived {verdict} != receipt verdict {receipt.get('verdict')}")
        else:
            out["trust"] = "signature valid (attestation only; could not re-derive)"
            out["messages"].append(detail)
    else:
        out["trust"] = "signature valid (attestation only)"
        out["messages"].append("claim class is attestation-only (e.g. Lean proof)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="numproof-verify",
                                 description="Offline re-checker for NumProof Verification Receipts.")
    ap.add_argument("receipt", nargs="?", help="path to receipt JSON (omit to read stdin)")
    ap.add_argument("--signer", help="expected NumProof signer address (0x...)")
    args = ap.parse_args(argv)
    raw = open(args.receipt, encoding="utf-8").read() if args.receipt else sys.stdin.read()
    receipt = json.loads(raw)
    r = recheck(receipt, expected_signer=args.signer)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    ok = r.get("signature_valid") and r.get("tamper_check") == "ok" and r.get("rederive_matches", True)
    print(("OK   " if ok else "FAIL ") + r.get("trust", ""), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

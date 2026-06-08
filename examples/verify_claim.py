"""Verify numeric/financial claims. Run: python examples/verify_claim.py
Set NUMPROOF_API_KEY for the paid API, or hit the free /demo via NumProof(base_url=...)."""
from numproof import NumProof

np = NumProof.from_env()

claims = [
    "120 + 90 + 340 + 15 == 565",
    "a 50% loss needs a 100% gain to break even",
    "two 10% raises equal a 21% total increase",
    "gross margin is 60% when gross profit is 600 and revenue is 1000",
    "LTV/CAC is 3 when LTV is 150 and CAC is 50",
    "operating cash flow grew 18% from 1000 to 1180",   # REFUTE: 1180/1000 = 18%? -> actually 18%, edit to taste
]

for c in claims:
    r = np.verify(c)
    print(f"{r['verdict']:8} | {c}")
    if r["verdict"] == "REFUTE":
        print(f"         | counterexample: {r.get('counterexample')}")

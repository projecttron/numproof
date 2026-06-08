"""Evaluate loan covenants / financial thresholds from labeled rows.
Run: python examples/covenants.py  (needs NUMPROOF_API_KEY)"""
from numproof import NumProof

np = NumProof.from_env()

# built-in starter packs: credit_covenants_basic, liquidity_basic, saas_margin_basic
rows = [["EBITDA", 500], ["Debt Service", 300], ["Debt", 1200], ["Cash", 100]]
bundle = np.covenant_rows(rows, rule_pack="credit_covenants_basic")
print("pack verdict:", bundle["verdict"], "| summary:", bundle["summary"])

# or supply your own rules
custom = [
    {"name": "DSCR >= 1.25", "numerator": "EBITDA", "denominator": "Debt Service", "op": ">=", "threshold": "1.25"},
    {"name": "Min cash 150",  "label": "Cash", "op": ">=", "threshold": "150"},
]
b2 = np.covenant_rows(rows, rules=custom)
print("custom verdict:", b2["verdict"], "| summary:", b2["summary"])

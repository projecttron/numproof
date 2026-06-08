"""Audit spreadsheet-like rows for arithmetic consistency with cell provenance.
Run: python examples/audit_spreadsheet.py  (needs NUMPROOF_API_KEY)"""
from numproof import NumProof

np = NumProof.from_env()

rows = [
    ["Product revenue", 1200000],
    ["Services revenue", 450000],
    ["Other revenue", 80000],
    ["Total revenue", 1730000],      # foots: 1.2M + 450k + 80k
    [],
    ["COGS line A", 300000],
    ["COGS line B", 215000],
    ["Total COGS", 500000],          # WRONG: 300k + 215k = 515k -> REFUTE
    [],
    ["Revenue", 1730000],
    ["Gross Profit", 1230000],
    ["Gross Margin", "71.1%"],
]

audit = np.audit_rows(rows)
print("verdict:", audit["verdict"], "| summary:", audit["summary"])
for c in audit["claims"]:
    flag = "x" if c["verdict"] == "REFUTE" else "+"
    print(f"  [{flag}] {c['verdict']:7} {c['description']}  (expected {c['expected']}, actual {c['actual']})")

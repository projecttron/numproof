"""NumProof Python client — a thin HTTP SDK for the hosted NumProof API.

No third-party dependencies (stdlib only). This client contains NO verification logic:
it just makes authenticated HTTP calls to the hosted NumProof service, which runs the
deterministic numeric/financial verification engine. (Same shape as `stripe-python`.)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["NumProof", "VerifyClient", "NumProofAPIError"]

DEFAULT_BASE_URL = "https://numproof.com"

Transport = Callable[[str, str, dict[str, str], "bytes | None", float], "tuple[int, dict[str, str], bytes]"]


class NumProofAPIError(RuntimeError):
    """Raised when the NumProof API returns a non-2xx response."""

    def __init__(self, status: int, body: Any):
        self.status = int(status)
        self.body = body
        super().__init__(f"NumProof API HTTP {self.status}: {body}")


def _default_transport(method: str, url: str, headers: dict[str, str],
                       data: "bytes | None", timeout: float) -> "tuple[int, dict[str, str], bytes]":
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()


def _decode_response(headers: dict[str, str], raw: bytes) -> Any:
    ctype = ""
    for key, value in headers.items():
        if key.lower() == "content-type":
            ctype = str(value).lower()
            break
    if "json" in ctype or raw[:1] in (b"{", b"["):
        return json.loads(raw.decode("utf-8"))
    return raw


@dataclass
class NumProof:
    """Client for the hosted NumProof API.

    >>> np = NumProof.from_env()              # reads NUMPROOF_BASE_URL / NUMPROOF_API_KEY
    >>> np.verify("120 + 90 + 340 + 15 == 565")["verdict"]
    'VERIFY'
    """

    base_url: str = DEFAULT_BASE_URL
    api_key: "str | None" = None
    timeout: float = 60.0
    transport: Transport = field(default=_default_transport, repr=False)

    @classmethod
    def from_env(cls, *, base_url_var: str = "NUMPROOF_BASE_URL",
                 api_key_var: str = "NUMPROOF_API_KEY") -> "NumProof":
        # NUMPROOF_* preferred; VERIFY_* accepted for backwards compatibility.
        base = os.environ.get(base_url_var) or os.environ.get("VERIFY_BASE_URL") or cls.base_url
        key = os.environ.get(api_key_var) or os.environ.get("VERIFY_API_KEY") or None
        return cls(base_url=base, api_key=key)

    def _url(self, path: str, query: "dict[str, str] | None" = None) -> str:
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _request(self, method: str, path: str, payload: "dict[str, Any] | None" = None,
                 *, query: "dict[str, str] | None" = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, headers, raw = self.transport(method, self._url(path, query), self._headers(), data, self.timeout)
        body = _decode_response(headers, raw)
        if status < 200 or status >= 300:
            raise NumProofAPIError(status, body)
        return body

    @staticmethod
    def _with_webhook(payload: dict[str, Any], webhook_url: str = "") -> dict[str, Any]:
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    # ---- claim verification ----
    def verify(self, claim: str, *, allow_llm: bool = False, webhook_url: str = "") -> dict[str, Any]:
        """Verify one numeric/financial claim -> VERIFY / REFUTE / ABSTAIN with a certificate."""
        return self._request("POST", "/verify", self._with_webhook(
            {"claim": claim, "allow_llm": allow_llm}, webhook_url))

    def batch(self, claims: list[str], *, allow_llm: bool = False, webhook_url: str = "") -> dict[str, Any]:
        """Verify many claims in one call (CI / agent workflows)."""
        return self._request("POST", "/verify/batch", self._with_webhook(
            {"claims": claims, "allow_llm": allow_llm}, webhook_url))

    def proof(self, claim: str, *, allow_llm: bool = False, webhook_url: str = "") -> dict[str, Any]:
        """Premium: machine-checkable formal (Lean) proof artifact for a supported claim."""
        return self._request("POST", "/verify/proof", self._with_webhook(
            {"claim": claim, "allow_llm": allow_llm}, webhook_url))

    # ---- spreadsheet workflows ----
    def audit_rows(self, rows: list[list[Any]], *, ratio_tolerance: str = "1/2000",
                   subtotal_tolerance: str = "0", source: str = "rows",
                   format: str = "json", webhook_url: str = "") -> Any:
        """Audit spreadsheet-like rows: footing, ties, margins, formula cells, with provenance.
        format='json'|'html'|'pdf'|'zip' (zip = signed bundle + human report)."""
        return self._request("POST", "/verify/audit", self._with_webhook(
            {"rows": rows, "ratio_tolerance": ratio_tolerance,
             "subtotal_tolerance": subtotal_tolerance, "source": source}, webhook_url),
            query={"format": format} if format != "json" else None)

    def diff_rows(self, rows_before: list[list[Any]], rows_after: list[list[Any]], *,
                  materiality_tolerance: str = "0", source_before: str = "before_rows",
                  source_after: str = "after_rows", format: str = "json", webhook_url: str = "") -> Any:
        """Diff two report versions by numeric row labels -> signed change bundle."""
        return self._request("POST", "/verify/diff", self._with_webhook(
            {"rows_before": rows_before, "rows_after": rows_after,
             "materiality_tolerance": materiality_tolerance,
             "source_before": source_before, "source_after": source_after}, webhook_url),
            query={"format": format} if format != "json" else None)

    def covenant_rows(self, rows: list[list[Any]], rules: "list[dict[str, Any]] | None" = None, *,
                      rule_pack: str = "", source: str = "rows", format: str = "json",
                      webhook_url: str = "") -> Any:
        """Evaluate covenant/threshold rules (DSCR, Debt/EBITDA, current ratio, custom) on labeled rows."""
        payload: dict[str, Any] = {"rows": rows, "source": source}
        if rules is not None:
            payload["rules"] = rules
        if rule_pack:
            payload["rule_pack"] = rule_pack
        return self._request("POST", "/verify/covenants", self._with_webhook(payload, webhook_url),
                             query={"format": format} if format != "json" else None)

    def rule_packs(self) -> dict[str, Any]:
        """List built-in covenant/threshold rule-pack templates."""
        return self._request("GET", "/rule-packs")

    # ---- evidence + account ----
    def verify_signature(self, audit: dict[str, Any]) -> dict[str, Any]:
        """Verify a signed audit/diff/covenant bundle (no private key needed)."""
        return self._request("POST", "/audit/verify", {"audit": audit})

    def balance(self) -> dict[str, Any]:
        return self._request("GET", "/balance")

    def usage(self, *, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/usage", query={"limit": str(int(limit))})


# Backwards-compatible alias.
VerifyClient = NumProof

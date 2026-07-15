"""Local data-loss-prevention pass for optional AI transfers.

The redactor runs before any cloud-provider call. It preserves analytical
numbers and structure while removing secrets and common client identifiers.
Only category/count metadata is safe to return to the browser.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SENSITIVE_KEY_PARTS = {
    "api_key", "apikey", "secret", "password", "access_token", "refresh_token",
    "client_name", "customer_name", "household_name", "account_name",
    "account_number", "account_id_external", "ssn", "tax_id", "email",
    "phone", "address", "routing_number", "iban",
    "prepared_for",
}
_PATTERNS = (
    ("api_key", re.compile(r"\b(?:sk-ant-api\d*|sk-(?:proj|live|test)?)-[A-Za-z0-9_\-]{12,}\b", re.I)),
    ("email", re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")),
    ("account_number", re.compile(r"\b(?:account|acct|routing)\s*(?:number|no\.?|#)?\s*[:=]?\s*[A-Z0-9\-]{6,}\b", re.I)),
    (
        "client_name",
        re.compile(
            r"\b(?:(?i:(?:my|our|the))\s+)?"
            r"(?i:(?:client|customer|household|account\s+holder|beneficiary|prospect))"
            r"(?:'s)?(?:\s+(?i:name))?\s*(?::|=|(?i:is)\s+|(?i:named)\s+)?"
            r"[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,3}\b"
        ),
    ),
    (
        "client_name",
        re.compile(
            r"\b(?i:(?:prepared\s+for|on\s+behalf\s+of))\s*[:=]?\s*"
            r"[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,3}\b"
        ),
    ),
    (
        "client_name",
        re.compile(
            r"\b(?i:(?:for|regarding|about|on\s+behalf\s+of))\s+"
            r"(?:(?i:the)\s+)?[A-Z][A-Za-z'\-]+"
            r"(?:\s+[A-Z][A-Za-z'\-]+){0,3}\s+"
            r"(?i:(?:household|family|trust|account|portfolio))\b"
        ),
    ),
)


def prepare(value: Any) -> tuple[Any, dict[str, Any]]:
    findings: list[dict[str, str]] = []
    identifiers = sorted(_sensitive_literals(value), key=len, reverse=True)
    sanitized = _redact(value, (), findings, identifiers)
    canonical = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), default=str)
    categories: dict[str, int] = {}
    for finding in findings:
        category = finding["category"]
        categories[category] = categories.get(category, 0) + 1
    return sanitized, {
        "disclosure_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "redaction_count": len(findings),
        "redaction_categories": categories,
        "redacted_fields": sorted({finding["path"] for finding in findings if finding["path"]}),
        "raw_values_returned": False,
        "review_required": True,
        "basis": "Local DLP ran before the optional provider transfer.",
    }


def _normalize_key(value: str) -> str:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalize_key(value)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _scalar_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [item for child in value.values() for item in _scalar_strings(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _scalar_strings(child)]
    return []


def _sensitive_literals(value: Any) -> set[str]:
    literals: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_sensitive_key(str(key)):
                for literal in _scalar_strings(child):
                    if len(literal) >= 3 and (not literal.isdigit() or len(literal) >= 6):
                        literals.add(literal)
            literals.update(_sensitive_literals(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            literals.update(_sensitive_literals(child))
    return literals


def _redact(
    value: Any,
    path: tuple[str, ...],
    findings: list[dict[str, str]],
    identifiers: list[str],
) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            child_path = path + (name,)
            if _is_sensitive_key(name):
                findings.append({"category": "sensitive_field", "path": ".".join(child_path)})
                out[name] = "[redacted:sensitive_field]"
            else:
                out[name] = _redact(child, child_path, findings, identifiers)
        return out
    if isinstance(value, (list, tuple)):
        return [
            _redact(child, path + (str(index),), findings, identifiers)
            for index, child in enumerate(value)
        ]
    if isinstance(value, str):
        text = value
        for category, pattern in _PATTERNS:
            def replace(_match, *, _category=category):
                findings.append({"category": _category, "path": ".".join(path)})
                return f"[redacted:{_category}]"
            text = pattern.sub(replace, text)
        for identifier in identifiers:
            pattern = re.compile(re.escape(identifier), re.I)
            if pattern.search(text):
                findings.append({"category": "client_identifier", "path": ".".join(path)})
                text = pattern.sub("[redacted:client_identifier]", text)
        return text
    return value

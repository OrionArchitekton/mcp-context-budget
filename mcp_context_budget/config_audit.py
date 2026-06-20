from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|api[_-]?key|password|passwd|credential|private[_-]?key)", re.IGNORECASE
)
TOKEN_VALUE_RE = re.compile(
    r"(plain(?:text)?[-_]?demo[-_]?token[-_][A-Za-z0-9_-]{8,}|"
    r"(?:ghp|github_pat|xoxb|sk)[-_][A-Za-z0-9_-]{12,}|"
    r"AKIA[A-Z0-9]{12,})"
)
SAFE_REFERENCE_RE = re.compile(r"^(\$\{[A-Za-z_][A-Za-z0-9_]*\}|op://.+|<redacted>|\*+)$")


@dataclass(frozen=True)
class AuditFinding:
    path: str
    severity: str
    secret_class: str
    length_bucket: str
    fingerprint: str

    def to_json(self) -> dict[str, str]:
        return {
            "path": self.path,
            "severity": self.severity,
            "secret_class": self.secret_class,
            "length_bucket": self.length_bucket,
            "fingerprint": self.fingerprint,
        }


def _length_bucket(value: str) -> str:
    length = len(value)
    if length < 16:
        return "<16"
    if length < 32:
        return "16-31"
    if length < 64:
        return "32-63"
    return "64+"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _is_safe_reference(value: str) -> bool:
    return bool(SAFE_REFERENCE_RE.match(value.strip()))


def _finding(path: str, secret_class: str, value: str) -> AuditFinding:
    return AuditFinding(
        path=path,
        severity="high",
        secret_class=secret_class,
        length_bucket=_length_bucket(value),
        fingerprint=_fingerprint(value),
    )


def _scan_value(path: str, key: str | None, value: str) -> list[AuditFinding]:
    if _is_safe_reference(value):
        return []
    findings: list[AuditFinding] = []
    if key is not None and SENSITIVE_KEY_RE.search(key) and len(value) >= 8:
        findings.append(_finding(path, "sensitive-key-literal", value))
    for match in TOKEN_VALUE_RE.finditer(value):
        findings.append(_finding(path, "token-pattern", match.group(0)))
    return findings


def _walk(value: Any, path: str = "$", key: str | None = None) -> list[AuditFinding]:
    if isinstance(value, dict):
        findings: list[AuditFinding] = []
        for raw_key, raw_value in sorted(value.items()):
            child_key = str(raw_key)
            findings.extend(_walk(raw_value, f"{path}.{child_key}", child_key))
        return findings
    if isinstance(value, list):
        findings = []
        for index, item in enumerate(value):
            findings.extend(_walk(item, f"{path}[{index}]", key))
        return findings
    if isinstance(value, str):
        return _scan_value(path, key, value)
    return []


def audit_config(config_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {config_path}: {exc}") from exc
    findings = _walk(payload)
    unique: dict[tuple[str, str, str], AuditFinding] = {}
    for finding in findings:
        unique[(finding.path, finding.secret_class, finding.fingerprint)] = finding
    ordered = sorted(unique.values(), key=lambda item: (item.path, item.secret_class))
    high = sum(1 for finding in ordered if finding.severity == "high")
    return {
        "config": str(config_path),
        "status": "FAIL" if high else "PASS",
        "findings": [finding.to_json() for finding in ordered],
        "counts": {"total": len(ordered), "high": high},
    }


def should_fail(report: dict[str, Any], fail_on: str) -> bool:
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    total = int(counts.get("total") or 0)
    high = int(counts.get("high") or 0)
    if fail_on == "none":
        return False
    if fail_on == "any":
        return total > 0
    if fail_on == "high":
        return high > 0
    raise ValueError("--fail-on must be high, any, or none")


def run_config_audit_demo() -> dict[str, Any]:
    literal = "plaintext-demo-token-1234567890"
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-config-audit-") as tmp:
        root = Path(tmp)
        config = root / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "command": "github-mcp",
                            "env": {
                                "GITHUB_TOKEN": literal,
                                "LINEAR_API_KEY": "${LINEAR_API_KEY}",
                            },
                            "args": ["--token", literal],
                            "tools": [
                                {
                                    "name": "get_issue",
                                    "description": "Retrieve issue",
                                    "inputSchema": {"type": "object"},
                                }
                            ],
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = audit_config(config)
        serialized = json.dumps(report, sort_keys=True)
    return {
        "config_audit_findings": report["counts"]["total"],
        "config_audit_high": report["counts"]["high"],
        "config_audit_secret_values_redacted": literal not in serialized,
        "config_audit_safe_reference_ignored": "LINEAR_API_KEY" not in serialized,
        "config_audit_status": (
            "PASS"
            if report["counts"]["high"] >= 1
            and literal not in serialized
            and "LINEAR_API_KEY" not in serialized
            else "FAIL"
        ),
    }

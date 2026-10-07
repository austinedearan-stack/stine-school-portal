#!/usr/bin/env python3
"""Lightweight secret scanner (spec §23: scan for accidentally committed secrets before every commit).

Runs in pre-commit, CI and the test suite. It complements (does not replace) gitleaks, which the
pre-commit configuration also runs when available.

Usage:  python scripts/secret_scan.py [--staged] [paths...]
Exit status 1 if a finding is reported. A line can be allow-listed with the comment
``secret-scan: allow`` *only* for documented test fixtures/placeholders.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_FILES = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(?!example$)[^/]+$"),
    re.compile(r"\.(pem|key|p12|pfx|sqlite3|dump|backup)$"),
    re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)$"),
]

PATTERNS = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Stripe live key": re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b"),
    "Django insecure key": re.compile(r"django-insecure-[^\s'\"]{20,}"),
    "default secret in variable expansion": re.compile(
        r"\$\{[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|KEY)[A-Z0-9_]*:?-[^}]+\}"
    ),
    "credential in URL": re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:[^/\s@'\"$]{6,}@[^\s'\"]+", re.IGNORECASE),
    # NAME = "literal" assignments for secret-looking names (python, yaml, shell, env)
    "hard-coded secret assignment": re.compile(
        r"""(?ix)
        \b[\w.]*(secret|password|passwd|api_?key|token|private_?key)[\w]*\b
        \s*[:=]\s*
        (?P<q>["']?)(?P<val>[^\s"'#]{8,})(?P=q)
        """
    ),
}

# Values that are clearly placeholders / references rather than secrets.
# "?NAME" is the tail of a shell required-variable check: ${DB_PASSWORD:?DB_PASSWORD required}
# "WORD_WORD" (upper-case words joined by underscores) is an enum/constant label, not a secret.
PLACEHOLDER = re.compile(
    r"^(\$\{?[A-Z_]+|\?[A-Z_]|[A-Z]+(_[A-Z]+)+$|<[^>]+>|change[-_]?me|replace[-_]?me|example|placeholder|your[-_]|x{6,}|\*{6,}|"
    r"os\.environ|env(_str|_bool|_int|\()|getenv|settings\.|request\.|self\.|form\.|cleaned_data|"
    r"None|True|False|\[REDACTED\]|get_random_secret_key|make_password|forms\.|models\.)",
    re.IGNORECASE,
)

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "staticfiles", "__pycache__", ".pytest_cache", "var"}
TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".html", ".js", ".css",
    ".sh", ".sql", ".conf", ".example", ".env", "",
}


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {c: value.count(c) for c in set(value)}
    return -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())


def candidate_files(staged: bool, explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    try:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if staged else ["git", "ls-files"]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout  # noqa: S603
        files = [ROOT / line for line in out.splitlines() if line]
        if files or staged:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            files.append(path)
    return files


def scan_file(path: Path) -> list[str]:
    findings = []
    rel = path.relative_to(ROOT).as_posix() if path.is_absolute() and ROOT in path.parents else path.as_posix()
    for rule in FORBIDDEN_FILES:
        if rule.search(rel):
            findings.append(f"{rel}: forbidden file type committed")
    if path.suffix not in TEXT_SUFFIXES or not path.exists():
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "secret-scan: allow" in line:
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if name == "hard-coded secret assignment":
                    value = match.group("val")
                    quoted = bool(match.group("q"))
                    if path.suffix == ".py" and not quoted:
                        continue  # unquoted RHS in Python is code (a call/name), not a literal
                    if "(" in value or PLACEHOLDER.match(value) or shannon_entropy(value) < 3.0:
                        continue
                findings.append(f"{rel}:{lineno}: possible {name}")
    return findings


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    explicit = [a for a in argv if not a.startswith("--")]
    findings: list[str] = []
    for path in candidate_files(staged, explicit):
        if path.name == "secret_scan.py":
            continue  # this file contains the patterns themselves
        findings.extend(scan_file(path))
    for finding in findings:
        print(finding)
    if findings:
        print(f"\nsecret scan: {len(findings)} potential secret(s) found. Remove them (and rotate if real).")
        return 1
    print("secret scan: no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

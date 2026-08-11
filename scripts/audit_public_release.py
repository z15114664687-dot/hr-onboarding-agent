#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Iterable


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_SCAN_BYTES = 5 * 1024 * 1024

EXCLUDED_DIRECTORIES = {
    ".audit-runtime",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "data",
    "htmlcov",
    "logs",
    "output",
    "test_artifacts",
    "uploads",
    "venv",
}

FORBIDDEN_NAMES = {
    ".DS_Store",
    ".env",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}

FORBIDDEN_SUFFIXES = {
    ".7z",
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".log",
    ".pem",
    ".pfx",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".tgz",
    ".zip",
}

REQUIRED_PATHS = (
    ".env.example",
    ".github/dependabot.yml",
    ".github/SECURITY_CONFIGURATION.md",
    ".github/workflows/ci.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/container.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/security.yml",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "LICENSE",
    "OSS_SECURITY_AUDIT.md",
    "PUBLIC_RELEASE_CHECKLIST.md",
    "README.md",
    "SECURITY.md",
    "docs/GITHUB_LAUNCH.md",
    "docs/RELEASE_NOTES_v0.1.0.md",
    "pyproject.toml",
    "requirements.txt",
)

CONTENT_PATTERNS = (
    (
        "private_key",
        re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
        "private key material",
    ),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "OpenAI-style API key"),
    ("github_token", re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}\b"), "GitHub token"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key"),
    (
        "local_path",
        re.compile(r"(?:/" r"Users/[^/\s]+/|/" r"home/[^/\s]+/|[A-Za-z]:\\\\" r"Users\\\\[^\\\s]+\\\\)"),
        "machine-specific user path",
    ),
    (
        "china_id",
        re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"),
        "Chinese identity-card-like number",
    ),
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str
    line: int | None = None


def audit_tree(
    root: Path,
    *,
    required_paths: Iterable[str] = REQUIRED_PATHS,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict[str, object]:
    root = root.resolve()
    findings: list[Finding] = []
    scanned_files = 0

    for required_path in required_paths:
        if not (root / required_path).is_file():
            findings.append(Finding("missing_required_file", required_path, "required release file is missing"))

    for path in _iter_release_entries(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(Finding("symlink", relative, "symbolic links are not allowed in the public snapshot"))
            continue
        if not path.is_file():
            continue
        scanned_files += 1
        if path.name in FORBIDDEN_NAMES or any(path.name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            findings.append(Finding("forbidden_file", relative, "runtime, credential, archive, or local artifact"))
        size = path.stat().st_size
        if size > max_file_bytes:
            findings.append(Finding("oversized_file", relative, f"file is {size} bytes; limit is {max_file_bytes}"))
        if size <= MAX_TEXT_SCAN_BYTES:
            findings.extend(_scan_text_file(path, relative))

    report = {
        "root": ".",
        "status": "pass" if not findings else "fail",
        "scanned_files": scanned_files,
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    return report


def _iter_release_entries(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRECTORIES for part in relative_parts):
            continue
        yield path


def _scan_text_file(path: Path, relative: str) -> list[Finding]:
    content = path.read_bytes()
    if b"\x00" in content[:8192]:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[Finding] = []
    for code, pattern, detail in CONTENT_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(Finding(code, relative, detail, line))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a clean public-release snapshot.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = audit_tree(args.root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(f"public-release audit: {report['status']} ({report['scanned_files']} files, {report['finding_count']} findings)")
    for finding in report["findings"]:
        location = f"{finding['path']}:{finding['line']}" if finding["line"] else finding["path"]
        print(f"- {finding['code']}: {location} — {finding['detail']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

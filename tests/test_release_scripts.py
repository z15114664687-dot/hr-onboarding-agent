from pathlib import Path
import re

import yaml

from scripts.audit_public_release import audit_tree


def _codes(report: dict[str, object]) -> set[str]:
    return {finding["code"] for finding in report["findings"]}


def test_public_release_audit_accepts_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Synthetic demo only.\n", encoding="utf-8")

    report = audit_tree(tmp_path, required_paths=())

    assert report["status"] == "pass"
    assert report["finding_count"] == 0


def test_public_release_audit_rejects_secret_like_value(tmp_path: Path) -> None:
    secret = "sk-" + "A" * 32
    (tmp_path / "leaked.txt").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")

    report = audit_tree(tmp_path, required_paths=())

    assert "openai_key" in _codes(report)


def test_public_release_audit_rejects_machine_path(tmp_path: Path) -> None:
    private_path = "/" + "Users/private-user/internal/file.txt"
    (tmp_path / "notes.md").write_text(private_path, encoding="utf-8")

    report = audit_tree(tmp_path, required_paths=())

    assert "local_path" in _codes(report)


def test_public_release_audit_rejects_database_archive_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "candidate.db").write_bytes(b"sqlite")
    (tmp_path / "export.zip").write_bytes(b"archive")
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / "linked").symlink_to(tmp_path / "README.md")

    report = audit_tree(tmp_path, required_paths=())

    assert _codes(report) >= {"forbidden_file", "symlink"}


def test_public_release_audit_rejects_oversized_file(tmp_path: Path) -> None:
    (tmp_path / "large.bin").write_bytes(b"x" * 33)

    report = audit_tree(tmp_path, required_paths=(), max_file_bytes=32)

    assert "oversized_file" in _codes(report)


def test_public_release_audit_reports_missing_required_file(tmp_path: Path) -> None:
    report = audit_tree(tmp_path, required_paths=("README.md",))

    assert "missing_required_file" in _codes(report)


def test_github_workflows_parse_pin_actions_and_use_safe_triggers() -> None:
    workflow_root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    workflows = sorted(workflow_root.glob("*.yml"))

    assert {path.name for path in workflows} == {
        "ci.yml",
        "codeql.yml",
        "container.yml",
        "dependency-review.yml",
        "security.yml",
    }
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        document = yaml.load(text, Loader=yaml.BaseLoader)
        assert isinstance(document, dict)
        assert "on" in document
        assert "pull_request_target" not in text
        assert "permissions: write-all" not in text
        assert "${{ github.event" not in text
        for action in re.findall(r"uses:\s*([^\s#]+)", text):
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), f"{workflow.name}: unpinned action {action}"


def test_dependabot_covers_runtime_actions_and_container() -> None:
    dependabot_path = Path(__file__).resolve().parents[1] / ".github" / "dependabot.yml"
    document = yaml.safe_load(dependabot_path.read_text(encoding="utf-8"))

    ecosystems = {update["package-ecosystem"] for update in document["updates"]}
    assert ecosystems == {"pip", "github-actions", "docker"}

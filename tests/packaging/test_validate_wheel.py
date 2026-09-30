"""Tests for release-wheel runtime-data validation."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from infrastructure.deployment.packaging.validate_wheel import missing_skill_files


def test_missing_wheel_reports_both_prompt_bases(tmp_path: Path) -> None:
    wheel_path = tmp_path / "opensre.whl"
    with ZipFile(wheel_path, "w"):
        pass
    missing = missing_skill_files(wheel_path)
    assert "core/agent_harness/prompts/opensre_system_prompt.md" in missing
    assert "core/agent_harness/prompts/gateway_system_prompt.md" in missing


def test_missing_skill_files_reports_absent_runtime_data(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_path = repo_root / "core/agent_harness/prompts/skills/example/SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("example", encoding="utf-8")
    manifest_path = repo_root / "infrastructure/deployment/packaging/release_manifest.py"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        "def required_skill_files(repo_root):\n"
        "    return (repo_root / "
        "'core/agent_harness/prompts/skills/example/SKILL.md',)\n",
        encoding="utf-8",
    )
    wheel_path = tmp_path / "opensre.whl"
    with ZipFile(wheel_path, "w"):
        pass

    assert missing_skill_files(wheel_path, repo_root=repo_root) == (
        "core/agent_harness/prompts/skills/example/SKILL.md",
    )


def test_missing_skill_files_accepts_complete_wheel(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    relative_skill_path = Path("tools/example/SKILL.md")
    skill_path = repo_root / relative_skill_path
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("example", encoding="utf-8")
    manifest_path = repo_root / "infrastructure/deployment/packaging/release_manifest.py"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        "def required_skill_files(repo_root):\n"
        "    return (repo_root / 'tools/example/SKILL.md',)\n",
        encoding="utf-8",
    )
    wheel_path = tmp_path / "opensre.whl"
    with ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(relative_skill_path.as_posix(), "example")

    assert missing_skill_files(wheel_path, repo_root=repo_root) == ()

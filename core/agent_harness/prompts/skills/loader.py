"""Action-agent skills: thin index in the harness, fat bodies on demand.

Skills are markdown files that teach the action planner how to map a
recognisable request onto a concrete tool sequence.

Layout (either form is supported):

- Package: ``skills/<name>/SKILL.md`` (preferred) or ``skills/<name>/<name>.md``,
  with an optional sibling ``<name>_report.md`` report template.
- Flat: ``skills/<name>.md`` with optional ``skills/<name>_report.md``.

Optional YAML frontmatter (``name``, ``description``, optional ``recurring``,
optional ``demo``) feeds the compact index and capability overview. Without
frontmatter, the name is derived from the path and the description from the
first ``WHEN TO USE`` / subtitle lines.

The harness prompt carries only :func:`load_skills_index` (~hundreds of
chars). Full bodies load through the ``skill_view`` tool via
:func:`load_skill_body`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from infrastructure.harness_providers.prompt_context import ActionPromptContext

_LOCAL_CONTEXT = ActionPromptContext("interactive_shell", None, frozenset())

__all__ = (
    "ActionSkill",
    "SKILLS_HEADER",
    "list_action_skills",
    "load_skill_body",
    "load_skills_block",
    "load_skills_demo_block",
    "load_skills_index",
    "skills_dir",
)

SKILLS_HEADER = f"{'=' * 40} SKILLS INDEX {'=' * 40}"

_PACKAGE_SKILL_FILENAME = "SKILL.md"
_REPORT_TEMPLATE_SUFFIX = "_report.md"
_REPO_SKILLS_PREFIX = "core/agent_harness/prompts/skills"
_REPORT_TEMPLATE_HEADER = "REPORT TEMPLATE from `{repo_path}` (fill exactly; keep all headings):"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BANNER_RE = re.compile(r"^[=\-─]{8,}\s*$")


@dataclass(frozen=True)
class ActionSkill:
    """One discoverable action-agent skill (index metadata + path)."""

    name: str
    description: str
    path: Path
    recurring: str | None = None
    demo: str | None = None
    gateway_platforms: frozenset[str] | None = None


def skills_dir() -> Path:
    """Return the directory that holds the bundled skill markdown files."""
    return Path(__file__).parent


def _repo_relative_path(path: Path) -> str:
    """Return a stable repo-relative path for prompt references."""
    try:
        relative = path.relative_to(skills_dir())
    except ValueError:
        return path.name
    return f"{_REPO_SKILLS_PREFIX}/{relative.as_posix()}"


def _package_skill_path(package_dir: Path) -> Path | None:
    """Return the skill recipe path inside a package directory, if present."""
    for candidate in (
        package_dir / _PACKAGE_SKILL_FILENAME,
        package_dir / f"{package_dir.name}.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def _iter_skill_paths(directory: Path) -> list[Path]:
    """Return skill recipe paths in stable order (packages then flat files)."""
    paths: list[Path] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_file = _package_skill_path(child)
        if skill_file is not None:
            paths.append(skill_file)
    paths.extend(sorted(directory.glob("*.md")))
    return paths


def _report_template_path(skill_path: Path) -> Path:
    """Return the sibling report template path for a skill recipe."""
    package_name = skill_path.parent.name
    if skill_path.name == _PACKAGE_SKILL_FILENAME:
        return skill_path.parent / f"{package_name}{_REPORT_TEMPLATE_SUFFIX}"
    return skill_path.with_name(f"{skill_path.stem}{_REPORT_TEMPLATE_SUFFIX}")


def _name_from_path(skill_path: Path) -> str:
    if skill_path.name == _PACKAGE_SKILL_FILENAME:
        stem = skill_path.parent.name
    else:
        stem = skill_path.stem
    return stem.replace("_", "-").lower()


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return {}, normalized.strip()
    end_index = normalized.find("\n---", 3)
    if end_index == -1:
        return {"gateway_platforms": []}, normalized.strip()
    yaml_content = normalized[4:end_index]
    body = normalized[end_index + 4 :].strip()
    try:
        loaded = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        return {"gateway_platforms": []}, normalized.strip()
    if loaded is None and not yaml_content.strip():
        return {}, body
    if not isinstance(loaded, dict):
        return {"gateway_platforms": []}, body
    return loaded, body


def _string_field(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _derive_description(body: str) -> str:
    """Best-effort one-liner when frontmatter has no description."""
    lines = [ln.strip() for ln in body.splitlines()]
    # Skip banner / title lines, then prefer WHEN TO USE bullets.
    in_when = False
    for line in lines:
        if not line or _BANNER_RE.match(line):
            continue
        upper = line.upper()
        if upper.startswith("WHEN TO USE"):
            in_when = True
            continue
        if in_when:
            if line.startswith("-"):
                text = line.lstrip("- ").strip()
                if text:
                    return text[:240]
            if line.startswith("Do NOT") or upper.startswith("HARD RULE"):
                break
            continue
        if line.endswith(":") and "SKILL" in upper:
            continue
        if upper.startswith("RECOGNIZE ") or upper.startswith("STEPS"):
            return line[:240]
        if "SKILL" in upper and len(line) < 120:
            # Subtitle under the banner, e.g. "weather + daily news briefing"
            cleaned = re.sub(r"^.*SKILL[^—\-]*[—\-]\s*", "", line, count=1).strip()
            return (cleaned or line)[:240]
    # Fallback: first non-empty non-banner line.
    for line in lines:
        if line and not _BANNER_RE.match(line):
            return line[:240]
    return "Action-agent skill"


def _skill_body_with_optional_template(skill_path: Path, body: str) -> str:
    if not body:
        return ""
    template_path = _report_template_path(skill_path)
    if not template_path.is_file():
        return body
    template = template_path.read_text(encoding="utf-8").strip()
    if not template:
        return body
    header = _REPORT_TEMPLATE_HEADER.format(repo_path=_repo_relative_path(template_path))
    return "".join((body, "\n\n", header, "\n\n", template))


def _load_action_skill(skill_path: Path) -> ActionSkill | None:
    try:
        raw = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter, body = _parse_frontmatter(raw)
    if not body.strip():
        return None
    name = _string_field(frontmatter.get("name")) or _name_from_path(skill_path)
    if not _SKILL_NAME_RE.match(name):
        name = _name_from_path(skill_path)
    description = _string_field(frontmatter.get("description")) or _derive_description(body)
    recurring = _string_field(frontmatter.get("recurring")) or None
    demo = _string_field(frontmatter.get("demo")) or None
    restriction: frozenset[str] | None = None
    if "gateway_platforms" in frontmatter:
        declared = frontmatter["gateway_platforms"]
        restriction = frozenset()
        if isinstance(declared, list) and all(
            isinstance(item, str) and item.strip() for item in declared
        ):
            restriction = frozenset(item.strip().lower() for item in declared)
    return ActionSkill(
        name=name,
        description=description,
        path=skill_path,
        recurring=recurring,
        demo=demo,
        gateway_platforms=restriction,
    )


@lru_cache(maxsize=1)
def _discover_action_skills() -> tuple[ActionSkill, ...]:
    """Return discovered action skills in stable path order."""
    directory = skills_dir()
    if not directory.is_dir():
        return ()
    skills: list[ActionSkill] = []
    seen_names: set[str] = set()
    for path in _iter_skill_paths(directory):
        skill = _load_action_skill(path)
        if skill is None or skill.name in seen_names:
            continue
        seen_names.add(skill.name)
        skills.append(skill)
    return tuple(skills)


@lru_cache(maxsize=1)
def _skill_catalog() -> Mapping[str, ActionSkill]:
    """Index discovered metadata once without exposing a mutable name map."""
    return MappingProxyType({skill.name: skill for skill in _discover_action_skills()})


def _view_key(context: ActionPromptContext) -> tuple[str, str | None]:
    if context.surface == "interactive_shell":
        return "interactive_shell", None
    platform = context.active_platform
    return "gateway", platform.strip().lower() if isinstance(platform, str) else None


def _skill_visible(skill: ActionSkill, surface: str, platform: str | None) -> bool:
    return (
        surface == "interactive_shell"
        or skill.gateway_platforms is None
        or (platform is not None and platform in skill.gateway_platforms)
    )


@lru_cache(maxsize=32)
def _visible_skills(surface: str, platform: str | None) -> tuple[ActionSkill, ...]:
    return tuple(
        skill for skill in _discover_action_skills() if _skill_visible(skill, surface, platform)
    )


def list_action_skills(context: ActionPromptContext = _LOCAL_CONTEXT) -> tuple[ActionSkill, ...]:
    """Return the catalog visible on the explicit surface/platform."""
    return _visible_skills(*_view_key(context))


def _index_line(skill: ActionSkill) -> str:
    recurring = f" [recurring: {skill.recurring}]" if skill.recurring else ""
    return f"- {skill.name} — {skill.description}{recurring}"


def load_skills_index(context: ActionPromptContext = _LOCAL_CONTEXT) -> str:
    """Return the compact SKILLS INDEX for the explicit surface/platform."""
    return _render_skills_index(*_view_key(context))


@lru_cache(maxsize=32)
def _render_skills_index(surface: str, platform: str | None) -> str:
    skills = _visible_skills(surface, platform)
    if not skills:
        return ""
    lines = [
        SKILLS_HEADER,
        "",
        "Compact catalog only — full skill bodies are NOT inlined here.",
        "Skill matches outrank a generic docs/how-to answer.",
        "Before answering, check this catalog for an action-shaped match",
        '(including "set up", "install", "onboard me", "demo", "audit", or "fix").',
        'Capability questions ("what can you do", "how can you help",',
        '"what tools do you have") are NOT a skill_view match. Answer them directly.',
        "When the user request matches a skill below, call skill_view(name) in",
        "THIS turn BEFORE emitting that skill's tool sequence. Do not invent",
        "steps from the one-line description alone.",
        "",
    ]
    lines.extend(_index_line(skill) for skill in skills)
    return "".join(("\n".join(lines), "\n\n"))


_CAPABILITY_OVERVIEW_RULE = (
    "When the user asks what you can do, what you're capable of, how you can "
    "help, what tools you have, or for a demo / getting-started suggestion: "
    "answer with ONLY the skill demos below. Do not list platform features, "
    "slash commands, AGENTS.md capabilities, or a generic coding-agent menu. "
    "Do not add a Want-me-to closer that invents a fifth action (no open-PR "
    "check, no /health, no /investigate). Offer the demos as copy-pasteable "
    "prompts the user can send next."
)


def load_skills_demo_block(context: ActionPromptContext = _LOCAL_CONTEXT) -> str:
    """Return the capability-overview rule and copy-pasteable skill demos."""
    return _render_skills_demo(*_view_key(context))


@lru_cache(maxsize=32)
def _render_skills_demo(surface: str, platform: str | None) -> str:
    demos = tuple(skill for skill in _visible_skills(surface, platform) if skill.demo)
    if not demos:
        return ""
    lines = [_CAPABILITY_OVERVIEW_RULE, ""]
    lines.extend(f"- {skill.demo}" for skill in demos)
    return "\n".join(lines)


def load_skills_block() -> str:
    """Return the skills section for the action envelope (the compact index).

    Historically this dumped every skill body. The harness is now thin: only
    the index is stable-cached; bodies load via ``skill_view``.
    """
    return load_skills_index()


def load_skill_body(name: str, *, context: ActionPromptContext = _LOCAL_CONTEXT) -> str:
    """Return one skill's full body (+ report template), or ``\"\"`` if unknown."""
    needle = name.strip().lower().replace("_", "-")
    if not needle:
        return ""
    skill = _skill_catalog().get(needle)
    if skill is None or not _skill_visible(skill, *_view_key(context)):
        return ""
    raw = skill.path.read_text(encoding="utf-8")
    _frontmatter, body = _parse_frontmatter(raw)
    return _skill_body_with_optional_template(skill.path, body)


def clear_skills_caches() -> None:
    """Drop cached discovery/index (tests mutate on-disk skills)."""
    _discover_action_skills.cache_clear()
    _skill_catalog.cache_clear()
    _visible_skills.cache_clear()
    _render_skills_index.cache_clear()
    _render_skills_demo.cache_clear()


# Back-compat for tests that call ``load_skills_block.cache_clear()``.
load_skills_block.cache_clear = clear_skills_caches  # type: ignore[attr-defined]

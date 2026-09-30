"""GitHub action recipes selected from the final offered tool names."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext

_RECIPES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset({"github_cli"}),
        """GITHUB CLI REQUESTS USE GITHUB TOOLS:
When the primary request is to create/list/view/edit/close/comment/assign/label/
merge/search issues, PRs or repos, call github_cli directly. This includes
github.com/owner/repo URLs and "from this info create an issue on GitHub".
Exception: GitHub issue/PR/repo operations as a standalone request use github_cli.
For diagnosing a crash/failure/outage across multiple sources, query all the
available matching evidence tools, then answer from their results.
Pass args after the gh binary and an optional owner/name repo.
Example: github_cli(args=["pr", "list", "--state", "open"]).
After the tool returns, reply briefly from its result summary.""",
    ),
    (
        frozenset({"get_github_star_history"}),
        """GITHUB STAR HISTORY:
For star history, day-by-day stars, stars gained or star velocity, use
get_github_star_history. Do not replace it with gh api stargazers scans,
which can undercount. Answer from the returned data.""",
    ),
)


def github_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit independent CLI and star-history recipes only when offered."""
    return "\n\n".join(
        text for requirements, text in _RECIPES if requirements <= context.offered_tool_names
    )


__all__ = ["github_action_prompt_fragment"]

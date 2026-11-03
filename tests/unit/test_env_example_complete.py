"""Card P3-10: `.env.example` names every setting, and holds no value that is one.

Two failures, in opposite directions, and both are cheap to make.

**A name missing from `.env.example`** is a setting nobody knows exists. The owner follows
the setup guide, the adapter reads an unset variable, and the failure surfaces as something
unrelated hours later.

**A value present in `.env.example`** is a secret in the repository. That is I12, and it is
the one mistake here that cannot be undone: a token in a commit has been disclosed the
moment it is pushed, whatever the next commit does. The second half of this file is
deliberately suspicious of anything that looks like a credential, including values nobody
meant as one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from dispute_agent.infra.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
EXAMPLE = ROOT / ".env.example"
RAW = EXAMPLE.read_text(encoding="utf-8")
LINES = RAW.splitlines()

#: `NAME=` or `NAME=value`, ignoring comments and blanks.
ENTRIES: dict[str, str] = {
    name.strip(): value.strip()
    for name, _, value in (
        line.partition("=") for line in LINES if line.strip() and not line.startswith("#")
    )
}

#: Settings whose value is a safe non-secret default rather than something to fill in. Each
#: is here because the default is the right answer for everyone, not because it is harmless
#: to publish a real one.
DEFAULTED = {
    "GRAPH_AUTHORITY",  # the consumers authority; identical for every personal account
    "GRAPH_TOKEN_CACHE_PATH",  # a path under the git-ignored data/ folder
    "ANTHROPIC_MODEL_HAIKU",  # a model id, published by Anthropic
    "APP_ENV",
    "KILL_SWITCH",
    "TELEMETRY_CAPTURE_CONTENT",
    "LLM_ALLOW_PAID_USAGE",
    "LLM_PRIMARY_SPEND_CAP_USD",
    "LLM_SECONDARY_SPEND_CAP_USD",
    "HTTP_TIMEOUT_S",
    "PROMPTS_ALLOW_INACTIVE",
}

#: Shapes that mean "somebody filled this in". Checked across the whole file, comments
#: included, because a pasted example usually lands in a comment.
CREDENTIAL_SHAPES = (
    r"sk-[A-Za-z0-9_-]{16,}",  # Anthropic / OpenAI style
    r"ATATT[A-Za-z0-9_-]{16,}",  # Atlassian API token
    r"eyJ[A-Za-z0-9_-]{16,}",  # a JWT
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # a GUID
)


def environ_reads() -> set[str]:
    """Every `os.environ[...]` / `os.getenv(...)` name in `src/`, found by parsing.

    By AST rather than by grep, so a name inside a docstring or a comment is not mistaken
    for a read. This project has already shipped one check that string-matched its own
    documentation and passed; parsing is the cheap way not to do it twice.
    """
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_environ(node.value):
                index = node.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    names.add(index.value)
            if isinstance(node, ast.Call) and _is_getenv(node.func) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
    return names


def _is_environ(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _is_getenv(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "getenv"


class TestEveryNameIsListed:
    def test_every_setting_field_appears(self) -> None:
        missing = {name.upper() for name in Settings.model_fields} - set(ENTRIES)
        assert not missing, f"read by Settings but not in .env.example: {sorted(missing)}"

    def test_every_direct_environ_read_appears(self) -> None:
        """A setting read straight from the environment bypasses `Settings`, so it bypasses
        redaction too. If this fails, the fix is usually to move it into `Settings`."""
        missing = environ_reads() - set(ENTRIES)
        assert not missing, f"read from os.environ but not in .env.example: {sorted(missing)}"

    def test_the_file_parsed_at_all(self) -> None:
        """Without this, a `.env.example` that stopped parsing would make every test above
        pass by comparing against nothing."""
        assert len(ENTRIES) >= 15


class TestNoValueLooksLikeASecret:
    """I12. Each check is a different shape of "somebody filled this in"."""

    @pytest.mark.parametrize("name", sorted(n for n in ENTRIES if n not in DEFAULTED))
    def test_the_value_is_empty(self, name: str) -> None:
        assert ENTRIES[name] == "", (
            f"{name} has a value in .env.example. Names only: a value here is in the "
            f"repository forever, and revoking is the only remedy (I12)."
        )

    def test_no_secret_named_setting_has_any_value(self) -> None:
        """Belt and braces. Whatever the allow-list above says, nothing whose name contains
        `token`, `key`, `secret` or `password` may carry a value.

        `_PATH` is excluded, and only `_PATH`: `GRAPH_TOKEN_CACHE_PATH` names a *file*, and
        the file is the secret rather than its location. That is not a loophole to widen -
        the protection for the file itself is the next test, which checks it sits somewhere
        git cannot see.
        """
        for name, value in ENTRIES.items():
            if name.endswith("_PATH"):
                continue
            if re.search(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL", name):
                assert value == "", f"{name} carries a value"

    def test_the_token_cache_lives_somewhere_git_ignores(self) -> None:
        """The MSAL cache holds a refresh token. Encrypting it at rest (ADR 0023) is no help
        at all if the file is also committed, so the directory has to be ignored - and that
        has to be checked rather than remembered."""
        cache = ENTRIES["GRAPH_TOKEN_CACHE_PATH"]
        assert cache, "the cache path must have a default; an unset one lands in the cwd"
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        folder = cache.split("/")[0]
        assert any(line.strip("/") == folder for line in ignored), (
            f"{cache} is not inside a git-ignored directory ({folder!r} is not in .gitignore)"
        )

    @pytest.mark.parametrize("pattern", CREDENTIAL_SHAPES)
    def test_no_credential_shaped_text_anywhere_in_the_file(self, pattern: str) -> None:
        assert re.search(pattern, RAW) is None, f"credential-shaped text matching {pattern}"

    def test_no_real_email_address(self) -> None:
        """A mailbox or seller address filled in here names a real person (I11)."""
        for name, value in ENTRIES.items():
            if "@" in value:
                assert value.endswith("example.invalid"), f"{name} names a real address"

    def test_the_header_says_the_rule(self) -> None:
        """The file is read by a person following the setup guide, so the rule has to be on
        it rather than only in a test."""
        header = "\n".join(LINES[:3]).lower()
        assert "names only" in header
        assert "never paste" in header


class TestTheSetupGuideStaysHonest:
    GUIDE = (ROOT / "docs" / "SETUP_CHANNELS.md").read_text(encoding="utf-8")

    def test_it_names_what_the_owner_must_create(self) -> None:
        for needed in ("Entra", "Jira", "API token", "TEST_SELLER_EMAIL"):
            assert needed in self.GUIDE

    @pytest.mark.parametrize("pattern", CREDENTIAL_SHAPES[:3])
    def test_it_contains_no_credential_shaped_text(self, pattern: str) -> None:
        """A screenshot cannot be checked, but a pasted token can."""
        assert re.search(pattern, self.GUIDE) is None

    def test_it_names_every_variable_the_owner_has_to_fill_in(self) -> None:
        """So following the guide leaves no blank behind."""
        owner_fills = {
            "GRAPH_CLIENT_ID",
            "GRAPH_MAILBOX",
            "JIRA_BASE_URL",
            "JIRA_EMAIL",
            "JIRA_API_TOKEN",
            "JIRA_PROJECT_KEY",
            "TEST_SELLER_EMAIL",
            "REVIEW_CONSOLE_TOKEN",
        }
        missing = {name for name in owner_fills if name not in self.GUIDE}
        assert not missing, f"setup guide never mentions: {sorted(missing)}"

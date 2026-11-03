"""Settings, read once, in one place (config.md section 7, I12, I14).

Every secret is a `SecretStr`, so printing a settings object or dropping one into a log line
renders `**********` rather than the value. That is the cheap half of I12. The expensive half
is `secret_values()`, which hands the redaction processor the actual strings to scan for —
because the way a secret really escapes is not someone logging the settings object, it is a
token appearing inside an exception message or a URL that nobody thought about.

Nothing here has a default that is a real value, and `.env` is git-ignored. `.env.example`
lists names only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "ci"]


class Settings(BaseSettings):
    """Environment and `.env`. Constructed by the composition root, passed down by argument."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = "local"
    kill_switch: bool = False

    # --- the two secrets the project is allowed to hold (CLAUDE.md I14) -------------------
    anthropic_api_key: SecretStr | None = None
    review_console_token: SecretStr | None = None

    # --- credentials for the real integrations, Phases 3 and 4b --------------------------
    jira_api_token: SecretStr | None = None
    applicationinsights_connection_string: SecretStr | None = None

    # --- non-secret settings the adapters need -------------------------------------------
    anthropic_model_haiku: str | None = None
    foundry_project_endpoint: str | None = None
    foundry_model_deployment: str | None = None
    graph_client_id: str | None = None
    graph_mailbox: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_project_key: str | None = None
    test_seller_email: str | None = None

    telemetry_capture_content: bool = Field(
        default=False,
        description="I14: prompt and response content stays out of telemetry unless a test "
        "run explicitly asks for it, and only when app_env is 'test'.",
    )

    def secret_values(self) -> frozenset[str]:
        """Every secret currently loaded, for the redaction processor to scan for.

        Short values are skipped. A one or two character "secret" would match half the words
        in a log line, and a redactor that mangles everything gets switched off, which is
        worse than one that misses a value that could not have been a real token anyway.
        """
        values = set()
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                secret = value.get_secret_value()
                if len(secret) >= 8:
                    values.add(secret)
        return frozenset(values)

    def content_capture_allowed(self) -> bool:
        """I14. Capturing content is a test-only affordance, and the check lives here."""
        return self.telemetry_capture_content and self.app_env == "test"

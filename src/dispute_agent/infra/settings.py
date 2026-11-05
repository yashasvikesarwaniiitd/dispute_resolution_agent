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

from datetime import date  # noqa: TC003 - pydantic-settings resolves it at runtime
from decimal import Decimal  # noqa: TC003 - as above
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    #: Google says a desktop OAuth client's secret is not confidential; it is held as one
    #: anyway, so it is redacted from logs like every other credential (ADR 0038).
    gmail_client_secret: SecretStr | None = None
    applicationinsights_connection_string: SecretStr | None = None

    # --- non-secret settings the adapters need -------------------------------------------
    anthropic_model_haiku: str | None = None
    foundry_project_endpoint: str | None = None
    foundry_model_deployment: str | None = None
    #: The Entra directory the pilot subscription lives in. Not a secret; pins the sign-in
    #: to it, so a work account already signed in on the laptop is never picked up.
    azure_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_mailbox: str | None = None
    #: The `consumers` authority, because the pilot account is a personal Outlook.com one
    #: (ADR 0023). A tenant authority here is the usual cause of AADSTS50194.
    graph_authority: str = "https://login.microsoftonline.com/consumers/"
    #: The file is a secret even though the path is not: it holds a refresh token, encrypted
    #: at rest (DPAPI on Windows). It lives under `data/`, which is git-ignored.
    graph_token_cache_path: str = "data/msal_cache.bin"
    #: Gmail through the Gmail API (ADR 0038): a desktop OAuth client, signed in once by
    #: `make gmail-login`. The cache file holds the refresh token, encrypted at rest.
    gmail_client_id: str | None = None
    gmail_mailbox: str | None = None
    gmail_token_cache_path: str = "data/gmail_token.bin"
    #: Seconds. Every network call has one (CLAUDE.md section 7); a call with no timeout is
    #: a worker that never comes back.
    http_timeout_s: float = 30.0
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_project_key: str | None = None
    test_seller_email: str | None = None

    # --- model spending and the prototype prompt (D18, ADR 0029) --------------------------
    #: The last day of the Azure free credit. After it, model calls stop unless
    #: `llm_allow_paid_usage` is on.
    azure_credit_expires_on: date | None = None
    llm_allow_paid_usage: bool = False
    llm_primary_spend_cap_usd: Decimal | None = None
    llm_secondary_spend_cap_usd: Decimal | None = None
    #: "I am knowingly testing a prompt that has not passed its eval suite." Off by default.
    prompts_allow_inactive: bool = False
    #: `single` (one model call per round) or `graph` (two independent passes reconciled by
    #: code, on LangGraph; ADR 0041). Off by default: `graph` costs two calls per round.
    reasoning_engine: Literal["single", "graph"] = "single"

    @field_validator(
        "azure_credit_expires_on",
        "llm_primary_spend_cap_usd",
        "llm_secondary_spend_cap_usd",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`.env.example` lists names with empty values; an empty value means not set."""
        return None if isinstance(value, str) and not value.strip() else value

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

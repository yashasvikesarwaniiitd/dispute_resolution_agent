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

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
    #: ADR 0057: Langfuse receives the AI-call spans. The public key is not a secret.
    langfuse_secret_key: SecretStr | None = None
    langfuse_public_key: str | None = None
    #: Langfuse's own dashboard calls it LANGFUSE_BASE_URL; either name works.
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("langfuse_host", "langfuse_base_url"),
    )
    #: ADR 0049: the live site's Foundry sign-in - a Microsoft Entra app registration (still
    #: Entra ID, I14). Set only in the host's secret store. Unset: the interactive sign-in.
    foundry_client_secret: SecretStr | None = None

    # --- non-secret settings the adapters need -------------------------------------------
    anthropic_model_haiku: str | None = None
    foundry_project_endpoint: str | None = None
    foundry_model_deployment: str | None = None
    #: The Entra directory the pilot subscription lives in. Not a secret; pins the sign-in
    #: to it, so a work account already signed in on the laptop is never picked up.
    azure_tenant_id: str | None = None
    #: ADR 0049: the app registration's id (not a secret) for the live site's Foundry sign-in.
    foundry_client_id: str | None = None
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
    #: Where the operations console posts a seller's dispute: the running API, loopback only
    #: (threat T09). The console never writes a case itself (ADR 0044).
    console_api_url: str = "http://127.0.0.1:8000"

    # --- the hosted console (ADR 0045) -------------------------------------------------------
    #: The one password in front of every screen of the hosted console. Required there: the
    #: hosted entry refuses to start without it. Unset on the owner's machine.
    console_password: SecretStr | None = None
    #: Gmail's refresh token, given as a secret where the encrypted file cannot travel (a
    #: Windows DPAPI file does not open on Linux). Held in memory only; `make gmail-export`
    #: copies it to the clipboard. Expires with the sign-in, every 7 days in Testing status.
    gmail_refresh_token: SecretStr | None = None
    #: Which modules file `config/` serves (default: modules.local.yaml, else modules.yaml).
    #: The hosted console sets `modules.hosted.yaml`: real Gmail and Jira, no secrets in it.
    modules_file: str | None = None

    # --- model spending and the prototype prompt (D18, ADR 0029) --------------------------
    #: The last day of the Azure free credit. After it, model calls stop unless
    #: `llm_allow_paid_usage` is on.
    azure_credit_expires_on: date | None = None
    llm_allow_paid_usage: bool = False
    llm_primary_spend_cap_usd: Decimal | None = None
    llm_secondary_spend_cap_usd: Decimal | None = None
    #: "I am knowingly testing a prompt that has not passed its eval suite." Off by default.
    prompts_allow_inactive: bool = False
    #: The model that reads a seller's emailed reply on the running app (ADR 0054). Unset:
    #: no reader, and every reply goes to a person. `anthropic_direct`: Claude Haiku with
    #: ANTHROPIC_API_KEY, behind the spending guard, against the Haiku route's own cap.
    reply_reader: Literal["anthropic_direct"] | None = None
    #: `single` (one model call per round), `graph` (two passes on LangGraph, ADR 0041) or
    #: `agent` (chooses its own read-only lookups, up to four turns, ADR 0042).
    #: ADR 0059; "investigator" (the agent chooses investigation tools) and "enriched" (code runs
    #: the look-ups, one call) are ADR 0072's, and need its inactive prompt v4.
    reasoning_engine: Literal[
        "single", "graph", "agent", "agent_v5", "investigator", "enriched", "plain"
    ] = "agent"
    #: "approved": the three approved demo sellers only (the frozen regression environment);
    #: "full": with the generated sellers (ADR 0072).
    seller_catalogue: Literal["full", "approved"] = "full"

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

    @field_validator("langfuse_host", mode="before")
    @classmethod
    def _blank_host_is_eu(cls, value: object) -> object:
        """`LANGFUSE_HOST=` as listed in `.env.example` means the default region, not ""."""
        return (
            "https://cloud.langfuse.com" if isinstance(value, str) and not value.strip() else value
        )

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

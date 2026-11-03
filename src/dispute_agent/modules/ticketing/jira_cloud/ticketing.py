"""Jira Cloud (integrations.md section 2, ADR 0020).

Jira has no idempotency key on create, and its search is eventually consistent. Those two
facts shape everything here, and the design does not pretend otherwise.

**Idempotency comes from our own record, never from a search.** The PLANNED action row with
its key is committed before any call. `idempotent_writes` is therefore `False`, which tells
the executor it may not retry a create blind: after a crash it returns UNKNOWN and a person
resolves it, using the label search below as a hint rather than as an answer. A "search by
label, then create if absent" design would race against indexing and produce a duplicate
issue on exactly the slow day when nobody is watching.

**A miss from `find_issue` proves nothing.** Atlassian say recent updates may take "a few
seconds to minutes" to appear. So a miss and "it does not exist" look identical from here,
and only a hit is information.

**The marker is set after create, in a second call.** If that second call fails the issue
exists and is unmarked, which is neither success nor failure: `AmbiguousOutcome` carries the
issue key so a person can find it. Reporting failure would invite a second create; reporting
success would lose it.

Descriptions are Atlassian Document Format, which the v3 API requires. Jira may restructure
the document on storage, so verification compares the **plain text** extracted from what
came back, normalised — never the ADF JSON.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import IssueRef, IssueSnapshot

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.external import IssueSpec, OpsFinding
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "ticketing"
ADAPTER = "jira_cloud"

API = "/rest/api/3"

#: Every issue this system creates carries it, so a person can find all of them at once.
AGENT_LABEL = "dispute-agent"

#: The property holding our full key. Entity properties are not searchable without an
#: indexing app, so this is for verification only - the label is what search can see.
PROPERTY_KEY = "dispute_agent"

RETRYABLE = frozenset({429, 500, 502, 503, 504})


def key_label(key: IdempotencyKey) -> str:
    """`wd1-<first 40 hex>`. Labels cannot contain spaces, and 44 characters stays well
    short of any plausible limit."""
    return f"wd1-{str(key).split(':')[-1][:40]}"


def normalise(text: str) -> str:
    """integrations.md 2.3. Both sides of the description comparison use it."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def to_adf(text: str) -> dict[str, Any]:
    """Plain text as an ADF document: one paragraph per blank-line-separated block.

    Built here rather than accepted from a caller, so nothing can hand Jira a document with
    a link, a mention or a macro in it. The description of a remeasurement request is text.

    **Blocks, not lines.** An earlier version made a paragraph out of every non-blank line,
    so a soft line break inside a paragraph came back as a paragraph break. That is a
    difference the adapter introduces itself, and since the executor verifies by comparing
    the text that comes back with the text it meant to send, it would have failed a
    perfectly correct issue. A line break inside a block is kept as a `hardBreak`, which is
    ADF's own way of saying the same thing.
    """
    blocks = [block for block in re.split(r"\n\s*\n", text) if block.strip()]
    return {
        "type": "doc",
        "version": 1,
        # The block is written as it was given. Trimming it here looked tidy and lost
        # leading indentation, which `normalise` does not remove - so the text came back
        # different from the text that was sent, on a difference the adapter made.
        "content": [_paragraph(block) for block in blocks]
        or [{"type": "paragraph", "content": []}],
    }


def _paragraph(block: str) -> dict[str, Any]:
    """One paragraph, with any internal line breaks kept as hard breaks."""
    content: list[dict[str, Any]] = []
    for index, line in enumerate(block.split("\n")):
        if index:
            content.append({"type": "hardBreak"})
        if line:
            content.append({"type": "text", "text": line})
    return {"type": "paragraph", "content": content}


def from_adf(doc: Any) -> str:
    """Pull the text back out, whatever structure Jira chose to store it in.

    Walks the tree for `text` nodes instead of assuming the shape that was sent, because
    the shape that comes back is explicitly not promised to be the same one.
    """
    if not isinstance(doc, dict):
        return str(doc or "")

    lines: list[str] = []

    def walk(node: Any, into: list[str]) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                into.append(str(node.get("text", "")))
                return
            if node.get("type") == "hardBreak":
                # A line break inside a paragraph, which is what `to_adf` writes one for.
                into.append("\n")
                return
            children = node.get("content", [])
            if node.get("type") == "paragraph":
                piece: list[str] = []
                for child in children:
                    walk(child, piece)
                into.append("".join(piece))
                return
            for child in children:
                walk(child, into)
        elif isinstance(node, list):
            for child in node:
                walk(child, into)

    walk(doc, lines)
    # Paragraphs are blocks, so they rejoin with a blank line between them - the inverse of
    # how `to_adf` split them.
    return "\n\n".join(line for line in lines if line.strip())


@dataclass
class JiraCloudTicketing:
    """Implements TicketingSystem against one Jira Cloud project."""

    base_url: str
    email: str
    #: A password in all but name: Jira Basic auth is email plus token, and the pair acts as
    #: the whole account. Kept out of repr, which a traceback with locals, `--showlocals`
    #: or an f-string of the adapter would otherwise print in full (Phase 3 review,
    #: finding 2 - it was printed in full).
    api_token: str = field(repr=False)
    project_key: str
    issue_type: str = "Task"
    timeout_s: float = 30.0
    name: str = MODULE
    #: Jira has no idempotency key on create (integrations.md 2.2). This must stay False:
    #: the executor reads it to decide whether a retry could produce a second issue.
    idempotent_writes: bool = False
    #: Weak. Hits are reliable; misses are not proof of absence.
    lookup_supported: bool = True
    client: httpx.Client | None = field(default=None, repr=False)

    # -- writing --------------------------------------------------------------------------

    def upsert_issue(self, spec: IssueSpec, key: IdempotencyKey) -> IssueRef:
        """Create, then mark. Deliberately not "search, then create".

        Searching first would look safer and be worse: a miss means nothing, so a slow
        index turns into a duplicate issue.
        """
        created = self._post(
            f"{API}/issue",
            json={
                "fields": {
                    "project": {"key": spec.project_key},
                    "issuetype": {"name": self.issue_type},
                    "summary": spec.summary,
                    "description": to_adf(spec.description),
                    "labels": [AGENT_LABEL, key_label(key)],
                }
            },
        )
        ref = IssueRef(key=str(created["key"]), id=str(created["id"]))

        try:
            self._put(
                f"{API}/issue/{ref.key}/properties/{PROPERTY_KEY}",
                json={"idempotency_key": str(key), "case_id": str(spec.case_id)},
            )
        except (TransientPortError, PermanentPortError, ExternalNotFound) as unmarked:
            # The issue exists; only the marker is missing. Neither success nor failure.
            raise AmbiguousOutcome(
                f"issue {ref.key} was created but could not be marked with its "
                "idempotency key; it exists and needs checking by hand",
                external_ref=ref.key,
            ) from unmarked

        return ref

    # -- looking up ------------------------------------------------------------------------

    def find_issue(self, key: IdempotencyKey) -> IssueRef | None:
        """Best effort, by label. A hit is reliable; a miss settles nothing."""
        jql = f'project = "{self.project_key}" AND labels = "{key_label(key)}"'
        payload = self._post(
            f"{API}/search/jql",
            json={"jql": jql, "maxResults": 2, "fields": ["id"]},
            # A POST that changes nothing. Without this, a slow search before a write would
            # read as an ambiguous write and send every such case to a person.
            read_only=True,
        )
        issues = payload.get("issues", [])
        if not issues:
            return None
        first = issues[0]
        return IssueRef(key=str(first["key"]), id=str(first["id"]))

    def get_issue(self, ref: IssueRef) -> IssueSnapshot:
        """Read-back (I2), compared against the stored intent by the executor."""
        raw = self._get(
            f"{API}/issue/{ref.key}",
            params={
                "fields": "summary,description,labels,status,project,resolution",
                "properties": PROPERTY_KEY,
            },
        )
        fields = raw.get("fields", {})
        status = (fields.get("status") or {}).get("name", "")
        resolution = (fields.get("resolution") or {}) or {}

        return IssueSnapshot(
            key=str(raw.get("key", ref.key)),
            project_key=str((fields.get("project") or {}).get("key", "")),
            labels=tuple(fields.get("labels", [])),
            summary=str(fields.get("summary", "")),
            # Text, not ADF: Jira may restructure the document on storage (2.3).
            description_text=normalise(from_adf(fields.get("description"))),
            status=str(status),
            resolution=str(resolution.get("name")) if resolution.get("name") else None,
            idempotency_key=_stored_key(raw),
        )

    def fetch_findings(self, since: datetime) -> Sequence[OpsFinding]:
        """Not implemented here: a finding is read from custom fields the owner configures.

        Returning an empty sequence would read as "operations have found nothing", which is
        a fact about a shipment rather than about this adapter. Raising says what is true.
        """
        raise NotImplementedError(
            "ops findings need the custom field ids from config/modules.yaml "
            "(integrations.md 2.4); the poller is card P3-07"
        )

    # -- http -------------------------------------------------------------------------------

    def _auth_header(self) -> str:
        raw = f"{self.email}:{self.api_token}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _client(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout_s)
        return self.client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, *, json: Any, read_only: bool = False) -> dict[str, Any]:
        return self._request("POST", path, json=json, read_only=read_only)

    def _put(self, path: str, *, json: Any) -> dict[str, Any]:
        return self._request("PUT", path, json=json)

    def _request(
        self, method: str, path: str, *, read_only: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        """Where an HTTP answer becomes a decision the executor acts on."""
        try:
            response = self._client().request(method, path, headers=self._headers(), **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as unsent:
            # The request never left this machine, so nothing happened at the far end.
            # This, and only this, is what TransientPortError promises the executor.
            raise TransientPortError(
                f"{method} {path} could not be sent: {type(unsent).__name__}"
            ) from unsent
        except httpx.TransportError as lost:
            if method == "GET" or read_only:
                # A read changes nothing, so a lost answer is safe to retry.
                raise TransientPortError(f"{method} {path} failed: {type(lost).__name__}") from lost
            # The write was dispatched and the answer was lost. It may well have happened -
            # Graph may have sent the email, Jira may have created the issue. Calling that
            # transient told the executor "certainly did not happen", which recorded a sent
            # email as FAILED and let a retry send a second copy (Phase 3 review, finding 1).
            raise AmbiguousOutcome(
                f"{method} {path}: the request was sent and no answer came back "
                f"({type(lost).__name__}); it may have taken effect"
            ) from lost

        if response.status_code in (401, 403):
            raise AuthPortError(
                f"Jira refused the request ({response.status_code}); check JIRA_EMAIL "
                "matches the account the token belongs to"
            )
        if response.status_code == 404:
            raise ExternalNotFound(f"{method} {path} returned 404")
        if response.status_code in RETRYABLE:
            raise TransientPortError(
                f"Jira returned {response.status_code}", retry_after_s=_retry_after(response)
            )
        if response.status_code >= 400:
            raise PermanentPortError(
                f"Jira refused {method} {path}: {_safe_detail(response)}",
                status=response.status_code,
            )

        if not response.content:
            return {}
        parsed = response.json()
        return dict(parsed) if isinstance(parsed, dict) else {}


def _stored_key(raw: dict[str, Any]) -> IdempotencyKey | None:
    from dispute_agent.kernel.domain.ids import IdempotencyKey as _Key

    for prop in raw.get("properties", []) if isinstance(raw.get("properties"), list) else []:
        if prop.get("key") == PROPERTY_KEY:
            value = (prop.get("value") or {}).get("idempotency_key")
            return _Key(str(value)) if value else None

    properties = raw.get("properties")
    if isinstance(properties, dict):
        value = (properties.get(PROPERTY_KEY) or {}).get("idempotency_key")
        return _Key(str(value)) if value else None
    return None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _safe_detail(response: httpx.Response) -> str:
    """A Jira error, trimmed. Never the request, which carries the Basic credential."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or str(response.status_code)
    if isinstance(body, dict):
        messages = body.get("errorMessages") or []
        if messages:
            return str(messages[0])
        errors = body.get("errors") or {}
        if isinstance(errors, dict) and errors:
            field_name, message = next(iter(errors.items()))
            return f"{field_name}: {message}"
    return str(response.status_code)


__all__ = [
    "ADAPTER",
    "AGENT_LABEL",
    "API",
    "MODULE",
    "PROPERTY_KEY",
    "RETRYABLE",
    "JiraCloudTicketing",
    "from_adf",
    "key_label",
    "normalise",
    "to_adf",
]

"""Card P3-03: the Jira adapter, driven against a simulated Jira.

The card names one test by hand — *a create that times out after Jira accepted it produces
one issue, not two* — and it is the one worth reading. Jira has no idempotency key, so the
adapter cannot make that true by itself. What it can do is tell the truth about its own
capability (`idempotent_writes = False`) and never search-then-create, so the executor knows
a retry is unsafe and never issues one. `TestARetryCannotProduceASecondIssue` tests that
chain rather than a single call.

`TestTheDescriptionSurvivesTheRoundTrip` is the other one with teeth: verification compares
plain text extracted from whatever ADF Jira hands back, because the structure that comes
back is explicitly not promised to be the structure that was sent.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import IssueRef, IssueSpec
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
from dispute_agent.modules.ticketing.jira_cloud.ticketing import (
    AGENT_LABEL,
    PROPERTY_KEY,
    JiraCloudTicketing,
    from_adf,
    key_label,
    normalise,
    to_adf,
)

CASE = CaseId("case_jira")
KEY = IdempotencyKey("wd1:" + "f" * 64)
PROJECT = "WD"
EMAIL = "owner@example.invalid"
JIRA_TOKEN = "an-api-token-that-is-a-password"

DESCRIPTION = "Please remeasure SHP-000001.\n\nDeclared 20 x 15 x 10 cm."

SPEC = IssueSpec(
    project_key=PROJECT,
    summary="Remeasure SHP-000001",
    description=DESCRIPTION,
    labels=(AGENT_LABEL,),
    case_id=CASE,
)


class Jira:
    """A scripted Jira. Records every request and answers from a route table."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: list[tuple[str, str, httpx.Response]] = []

    def on(self, method: str, contains: str, response: httpx.Response) -> Jira:
        self.routes.append((method, contains, response))
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        matches = [
            (path.index(contains), response)
            for method, contains, response in self.routes
            if request.method == method and contains in path
        ]
        if matches:
            return max(matches, key=lambda m: m[0])[1]
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    def system(self) -> JiraCloudTicketing:
        client = httpx.Client(
            base_url="https://example.atlassian.net",
            transport=httpx.MockTransport(self.handler),
        )
        return JiraCloudTicketing(
            base_url="https://example.atlassian.net",
            email=EMAIL,
            api_token=JIRA_TOKEN,
            project_key=PROJECT,
            client=client,
        )

    def body_of(self, index: int = 0) -> dict[str, Any]:
        return dict(json.loads(self.requests[index].content))

    def creates(self) -> int:
        return len(
            [r for r in self.requests if r.method == "POST" and r.url.path.endswith("/issue")]
        )


def made(key: str = "WD-1", issue_id: str = "10001") -> httpx.Response:
    return httpx.Response(201, json={"key": key, "id": issue_id})


def marked() -> httpx.Response:
    return httpx.Response(200, json={})


class TestCreating:
    def test_it_creates_then_marks(self) -> None:
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        ref = jira.system().upsert_issue(SPEC, KEY)

        assert ref.key == "WD-1"
        assert [r.method for r in jira.requests] == ["POST", "PUT"]

    def test_the_issue_carries_both_labels(self) -> None:
        """The agent label finds every issue this system made; the key label is what the
        best-effort search looks for."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        jira.system().upsert_issue(SPEC, KEY)

        labels = jira.body_of(0)["fields"]["labels"]
        assert AGENT_LABEL in labels
        assert key_label(KEY) in labels

    def test_the_key_label_has_no_spaces_and_stays_short(self) -> None:
        """Jira labels cannot contain spaces, and the maximum length is not documented."""
        label = key_label(KEY)
        assert " " not in label
        assert len(label) <= 44

    def test_the_full_key_goes_in_the_property_not_the_label(self) -> None:
        """The label is truncated so it stays a legal label; the property holds the whole
        key, which is what verification compares."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        jira.system().upsert_issue(SPEC, KEY)

        assert jira.body_of(1)["idempotency_key"] == str(KEY)
        assert PROPERTY_KEY in str(jira.requests[1].url)

    def test_the_description_is_sent_as_adf(self) -> None:
        """The v3 API requires it; a plain string is rejected."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        jira.system().upsert_issue(SPEC, KEY)

        description = jira.body_of(0)["fields"]["description"]
        assert description["type"] == "doc"
        assert description["version"] == 1

    def test_it_never_searches_before_creating(self) -> None:
        """ "Search, then create if absent" looks safer and is worse: a miss means nothing,
        so a slow index turns into a duplicate issue (ADR 0020)."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        jira.system().upsert_issue(SPEC, KEY)
        assert not any("search" in str(r.url) for r in jira.requests)


class TestTheMarkerFailing:
    def test_an_unmarked_issue_is_ambiguous_and_names_itself(self) -> None:
        """The issue exists and only the marker is missing. Reporting failure would invite
        a second create; reporting success would lose it."""
        jira = (
            Jira().on("POST", "/issue", made("WD-7")).on("PUT", "/properties", httpx.Response(503))
        )
        with pytest.raises(AmbiguousOutcome) as ambiguous:
            jira.system().upsert_issue(SPEC, KEY)
        assert ambiguous.value.external_ref == "WD-7"

    def test_the_issue_is_not_deleted_on_the_way_out(self) -> None:
        """Deleting it would destroy the thing a person has to go and look at."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", httpx.Response(500))
        with pytest.raises(AmbiguousOutcome):
            jira.system().upsert_issue(SPEC, KEY)
        assert not any(r.method == "DELETE" for r in jira.requests)


class TestARetryCannotProduceASecondIssue:
    """The card's named test, and the whole of ADR 0020.

    The adapter cannot make a create idempotent - Jira offers nothing to make it with. What
    it can do is be honest, so the executor never retries one.
    """

    def test_it_declares_that_writes_are_not_idempotent(self) -> None:
        assert Jira().system().idempotent_writes is False

    def test_calling_it_twice_really_does_make_two(self) -> None:
        """Which is why the flag above has to be False. A fake that deduplicated here
        would make the executor's caution look unnecessary."""
        jira = Jira().on("POST", "/issue", made()).on("PUT", "/properties", marked())
        system = jira.system()
        system.upsert_issue(SPEC, KEY)
        system.upsert_issue(SPEC, KEY)
        assert jira.creates() == 2

    def test_a_create_that_times_out_leaves_the_decision_to_the_executor(self) -> None:
        """The adapter raises transient and makes no second attempt of its own. Whether
        anything was created is unknowable from here, and guessing is what produces the
        duplicate."""
        jira = Jira().on("POST", "/issue", httpx.Response(504))
        with pytest.raises(TransientPortError):
            jira.system().upsert_issue(SPEC, KEY)
        assert jira.creates() == 1


class TestLookupIsBestEffort:
    def test_a_hit_returns_the_issue(self) -> None:
        found = httpx.Response(200, json={"issues": [{"key": "WD-1", "id": "10001"}]})
        jira = Jira().on("POST", "/search/jql", found)
        ref = jira.system().find_issue(KEY)
        assert ref is not None
        assert ref.key == "WD-1"

    def test_a_miss_returns_none_and_means_nothing(self) -> None:
        """Indexing lags by "a few seconds to minutes", so a miss and "it does not exist"
        look identical from here. Only a hit is information."""
        jira = Jira().on("POST", "/search/jql", httpx.Response(200, json={"issues": []}))
        assert jira.system().find_issue(KEY) is None

    def test_the_search_is_scoped_to_the_project_and_the_key_label(self) -> None:
        jira = Jira().on("POST", "/search/jql", httpx.Response(200, json={"issues": []}))
        jira.system().find_issue(KEY)
        jql = jira.body_of(0)["jql"]
        assert PROJECT in jql
        assert key_label(KEY) in jql

    def test_it_uses_the_endpoint_that_is_not_being_removed(self) -> None:
        """`/rest/api/3/search` is "currently being removed"; the replacement is
        `/search/jql`."""
        jira = Jira().on("POST", "/search/jql", httpx.Response(200, json={"issues": []}))
        jira.system().find_issue(KEY)
        assert jira.requests[0].url.path.endswith("/search/jql")

    def test_the_search_is_bounded(self) -> None:
        """An unbounded search against a busy project is a slow request that eventually
        becomes a timeout, and then lookups stop working exactly when they are needed."""
        jira = Jira().on("POST", "/search/jql", httpx.Response(200, json={"issues": []}))
        jira.system().find_issue(KEY)
        assert jira.body_of(0)["maxResults"] <= 50


class TestTheDescriptionSurvivesTheRoundTrip:
    def test_text_goes_out_and_comes_back(self) -> None:
        assert normalise(from_adf(to_adf(DESCRIPTION))) == normalise(DESCRIPTION)

    def test_it_reads_a_structure_it_did_not_send(self) -> None:
        """Jira may restructure ADF on storage, so the reader walks for text nodes rather
        than assuming the shape that was written."""
        restructured = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Please remeasure "},
                        {"type": "text", "text": "SHP-000001."},
                    ],
                }
            ],
        }
        assert from_adf(restructured) == "Please remeasure SHP-000001."

    def test_an_empty_description_does_not_crash_the_reader(self) -> None:
        assert from_adf(None) == ""
        assert from_adf({"type": "doc", "version": 1, "content": []}) == ""

    @pytest.mark.parametrize(
        "text",
        [
            "Just one line.",
            "Two paragraphs.\n\nSecond one.",
            "A soft break\nwithin one paragraph.",
            "Title\nsubtitle\n\nBody line one\nBody line two",
            "A\n\n\n\nB",
            "",
        ],
    )
    def test_the_round_trip_is_exact_for_every_shape(self, text: str) -> None:
        """Checked over shapes rather than on one example, because the failure was shape
        dependent: paragraphs survived and a soft line break inside one did not. The
        executor compares the text that comes back with the text it sent, so any difference
        the adapter introduces itself fails a correct issue."""
        assert normalise(from_adf(to_adf(text))) == normalise(text)

    @given(
        st.lists(
            st.lists(
                st.text(
                    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
                    min_size=1,
                    max_size=40,
                ).filter(lambda line: line.strip() != ""),
                min_size=1,
                max_size=4,
            ),
            min_size=0,
            max_size=4,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_the_round_trip_is_exact_for_generated_text(self, blocks: list[list[str]]) -> None:
        """Hypothesis over paragraphs of lines. Catches the shapes nobody thought to list."""
        text = "\n\n".join("\n".join(lines) for lines in blocks)
        assert normalise(from_adf(to_adf(text))) == normalise(text)


class TestReadBack:
    def issue(self, **overrides: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "summary": SPEC.summary,
            "description": to_adf(DESCRIPTION),
            "labels": [AGENT_LABEL, key_label(KEY)],
            "status": {"name": "To Do"},
            "project": {"key": PROJECT},
            "resolution": None,
        }
        fields.update(overrides.pop("fields", {}))
        raw = {
            "key": "WD-1",
            "fields": fields,
            "properties": {PROPERTY_KEY: {"idempotency_key": str(KEY)}},
        }
        raw.update(overrides)
        return raw

    def test_it_returns_what_was_asked_for(self) -> None:
        jira = Jira().on("GET", "/issue/WD-1", httpx.Response(200, json=self.issue()))
        snapshot = jira.system().get_issue(IssueRef(key="WD-1", id="10001"))
        assert snapshot.summary == SPEC.summary
        assert snapshot.project_key == PROJECT
        assert normalise(snapshot.description_text) == normalise(DESCRIPTION)

    def test_the_idempotency_key_comes_back(self) -> None:
        """What proves this issue is ours rather than one that looks like it."""
        jira = Jira().on("GET", "/issue/WD-1", httpx.Response(200, json=self.issue()))
        assert jira.system().get_issue(IssueRef(key="WD-1", id="1")).idempotency_key == KEY

    def test_a_resolution_is_reported_so_a_duplicate_can_be_spotted(self) -> None:
        """Verification refuses an issue resolved as a duplicate (2.3)."""
        raw = self.issue(fields={"resolution": {"name": "Duplicate"}})
        jira = Jira().on("GET", "/issue/WD-1", httpx.Response(200, json=raw))
        assert jira.system().get_issue(IssueRef(key="WD-1", id="1")).resolution == "Duplicate"

    def test_a_missing_issue_raises_external_not_found(self) -> None:
        """A 404 within the verification window is "not yet", not "wrong"."""
        jira = Jira()
        with pytest.raises(ExternalNotFound):
            jira.system().get_issue(IssueRef(key="WD-9", id="0"))

    def test_it_asks_for_the_property_as_well_as_the_fields(self) -> None:
        jira = Jira().on("GET", "/issue/WD-1", httpx.Response(200, json=self.issue()))
        jira.system().get_issue(IssueRef(key="WD-1", id="1"))
        assert PROPERTY_KEY in str(jira.requests[0].url)


class TestErrorMapping:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retryable_statuses_are_transient(self, status: int) -> None:
        jira = Jira().on("POST", "/issue", httpx.Response(status))
        with pytest.raises(TransientPortError):
            jira.system().upsert_issue(SPEC, KEY)

    def test_a_400_is_permanent_and_never_retried(self) -> None:
        """Scenario S12c. Retrying an argument we got wrong gets the same answer."""
        refusal = httpx.Response(400, json={"errors": {"summary": "Summary is required."}})
        jira = Jira().on("POST", "/issue", refusal)
        with pytest.raises(PermanentPortError) as refused:
            jira.system().upsert_issue(SPEC, KEY)
        assert not isinstance(refused.value, TransientPortError)
        assert "summary" in str(refused.value)

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_are_auth_errors(self, status: int) -> None:
        jira = Jira().on("POST", "/issue", httpx.Response(status))
        with pytest.raises(AuthPortError) as failed:
            jira.system().upsert_issue(SPEC, KEY)
        assert "JIRA_EMAIL" in str(failed.value)

    def test_a_timeout_is_transient(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        client = httpx.Client(
            base_url="https://example.atlassian.net",
            transport=httpx.MockTransport(timeout),
        )
        system = JiraCloudTicketing(
            base_url="https://example.atlassian.net",
            email=EMAIL,
            api_token=JIRA_TOKEN,
            project_key=PROJECT,
            client=client,
        )
        with pytest.raises(TransientPortError):
            system.find_issue(KEY)


class TestFindingsAreNotSilentlyEmpty:
    def test_it_refuses_rather_than_reporting_nothing_found(self) -> None:
        """An empty sequence would read as "operations have found nothing", which is a
        statement about a shipment. Raising says what is actually true: this adapter cannot
        answer yet."""
        from datetime import UTC, datetime

        with pytest.raises(NotImplementedError, match=r"modules.yaml"):
            Jira().system().fetch_findings(datetime(2026, 3, 1, tzinfo=UTC))


class TestTheTokenIsAPassword:
    """I12. Jira's Basic auth means email plus token acts as the whole account."""

    def test_it_is_sent_as_basic_auth(self) -> None:
        jira = Jira().on("POST", "/search/jql", httpx.Response(200, json={"issues": []}))
        jira.system().find_issue(KEY)
        header = jira.requests[0].headers["Authorization"]
        assert header.startswith("Basic ")
        assert base64.b64decode(header[6:]).decode() == f"{EMAIL}:{JIRA_TOKEN}"

    @pytest.mark.parametrize("status", [400, 429, 500])
    def test_no_error_message_contains_the_token(self, status: int) -> None:
        jira = Jira().on("POST", "/issue", httpx.Response(status, json={"errorMessages": ["no"]}))
        with pytest.raises((PermanentPortError, TransientPortError)) as failure:
            jira.system().upsert_issue(SPEC, KEY)
        assert JIRA_TOKEN not in str(failure.value)
        assert "Basic" not in str(failure.value)

    def test_the_repr_does_not_carry_the_client(self) -> None:
        assert "Authorization" not in repr(Jira().system())

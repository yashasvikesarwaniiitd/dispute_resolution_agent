# Security review (Phase 6, P6-08)

Date: 2026-11-04. Scope: `docs/spec/threat-model.md` section 4, invariants I11, I12, I14.

The threat model names tests by test-charter ids (`TC-R06-*`, `TC-APR-01..06`), and almost no
test carries those ids, so its claim that every threat has a test could not be checked as
written. This review replaces the claim with an explicit map from each threat to tests that
exist, and `tests/unit/test_threat_coverage.py` fails the build if a threat is missing from the
map or a named test does not exist. Gaps found by the review are listed honestly below; the ones
that were missing behaviour, not just a missing test, were built.

## Threat to test map

| Threat | Tests | What they prove |
|--------|-------|-----------------|
| T01 | `tests/contract/test_prompt_registry.py::TestTrustBoundary::test_neutralise_handles_each_attack_shape`<br>`tests/evals/injection/test_corpus_routes.py::TestTheDisputeStatementRoute::test_a_fully_obedient_model_still_cannot_get_a_credit`<br>`tests/unit/policy/test_credit_and_signal_rules.py::TestModelSignals::test_r21_any_manipulation_signal_escalates` | the fence defuses every attack shape; a fully fooled model still cannot credit; any manipulation signal escalates |
| T02 | `tests/evals/injection/test_corpus_routes.py::TestTheShipmentNoteRoute::test_a_note_cannot_steer_the_case`<br>`tests/e2e/test_phase4p_agent.py::TestShipmentNotesReachTheAgentFenced::test_the_planted_notes_are_shown_inside_untrusted_blocks_and_defused` | notes and statements reach the model only inside defused untrusted blocks |
| T03 | `tests/e2e/drills/test_security_gaps.py::TestT03AnUnauthenticatedSenderEscalates::test_a_reply_that_failed_dmarc_sends_the_case_to_a_person`<br>`tests/e2e/test_inbound_review_fixes.py::TestM2RoutingIsExact::test_a_reply_into_the_thread_from_someone_else_is_not_routed_by_the_thread`<br>`tests/unit/modules/email/test_poller.py::TestSenderAuthentication::test_a_dmarc_failure_is_a_fail` | a reply that failed sender authentication escalates `sender_unauthenticated`; a stranger in the thread is not routed by it |
| T04 | `tests/unit/policy/test_rules.py::TestR06RecipientAllowlisted::test_an_unlisted_recipient_is_denied`<br>`tests/unit/policy/test_rules.py::TestR06RecipientAllowlisted::test_a_recipient_that_is_not_the_case_seller_is_denied`<br>`tests/e2e/drills/test_security_gaps.py::TestT04ARecipientChangedOnTheWayIsNeverVerified::test_a_read_back_to_someone_else_is_unknown_not_verified` | unlisted and non-seller recipients are denied; a read-back to someone else is never verified |
| T05 | `tests/unit/orchestrator/test_intake.py::TestDeduplication::test_the_same_event_twice_yields_one_inbox_row`<br>`tests/e2e/test_inbound_events.py::TestASellerReply::test_the_same_reply_twice_is_one_reply`<br>`tests/unit/policy/test_credit_and_signal_rules.py::TestR25ApprovalFresh::test_a_stale_approval_is_denied` | replays are deduplicated; stale approvals are denied |
| T06 | `tests/unit/policy/test_rules.py::TestR17CreditNotDuplicate::test_a_live_credit_elsewhere_denies`<br>`tests/unit/executor/test_executor.py::TestIdempotentExecution::test_a_replay_finds_the_original_and_does_not_send_again`<br>`tests/unit/infra/test_schema_constraints.py::TestCreditsNeedApprovals::test_only_one_live_credit_per_shipment_and_reason` | one credit per shipment and reason, by rule, by lookup and by the database |
| T07 | `tests/unit/policy/test_credit_and_signal_rules.py::TestR15AmountDeterministic::test_one_paisa_off_is_denied`<br>`tests/e2e/test_credit_gate.py::TestTheExecutorRefuses::test_an_approval_for_150_00_cannot_serve_an_intent_of_150_01` | only the calculated amount; the approval binds the amount |
| T08 | `tests/e2e/test_inbound_events.py::TestApprovals::test_a_decision_on_text_the_reviewer_did_not_see_is_ignored`<br>`tests/unit/modules/approvals/test_review_console.py::TestTheApprovalIsBoundToTheText::test_an_approval_does_not_cover_different_text` | a decision on text the reviewer did not see is ignored |
| T09 | `tests/e2e/drills/test_security_gaps.py::TestT09OnlyAnApproverCanDecide::test_a_decision_by_a_person_not_on_the_list_moves_no_money`<br>`tests/unit/modules/approvals/test_review_console.py::TestTheReviewerComesFromTheSession::test_there_is_no_parameter_for_the_actor`<br>`tests/unit/api/test_full_runtime.py::test_a_person_s_decision_is_refused_on_the_open_endpoint` | only listed approvers decide; the actor comes from the session; the open API refuses people's decisions |
| T10 | `tests/unit/infra/test_redaction.py::TestTheWholeLine::test_a_realistic_failure_line_leaks_nothing`<br>`tests/architecture/test_no_secrets.py::test_no_file_holds_a_credential_of_this_shape`<br>`tests/unit/modules/test_observability_and_shields.py::TestSpansCarryNoContent::test_a_traced_run_never_exports_content_or_secrets` | redaction, secret scanning, content-free spans |
| T11 | `tests/unit/gateway/test_budgeted_gateway.py::TestPerCaseCap::test_the_call_after_the_cap_never_reaches_the_model`<br>`tests/unit/gateway/test_budgeted_gateway.py::TestSpendCap::test_spend_at_the_cap_refuses_the_next_call`<br>`tests/e2e/test_skeleton_run.py::TestTheStepLimit::test_a_case_that_never_rests_is_stopped` | call and spend caps; the step limit |
| T12 | `tests/e2e/test_phase4p_agent.py::TestABadAnswerGoesToAPerson::test_twice_bad_escalates_and_files_nothing`<br>`tests/unit/policy/test_rules.py::TestR01ActionOnMenu::test_an_off_menu_proposal_is_denied`<br>`tests/unit/policy/test_r09_numbers.py::TestTheCatalogueNegatives::test_a_figure_with_no_fact_of_that_value_is_denied` | bad output escalates; off-menu is denied; unproven numbers are denied |
| T13 | `tests/unit/modules/test_foundry_agent_service.py::TestDriftCheck::test_instructions_edited_in_the_portal_are_refused`<br>`tests/unit/modules/test_foundry_agent_service.py::TestDriftCheck::test_a_prompt_changed_in_the_repo_since_the_sync_is_refused` | drift in the portal or the repo is refused |
| T14 | `tests/unit/gateway/test_failover_matrix.py::TestTheRowsThatNeverFailOver::test_the_secondary_is_never_called`<br>`tests/unit/gateway/test_failover_matrix.py::TestTheRowsThatFailOver::test_an_unavailable_primary_is_served_by_the_secondary` | only availability fails over |
| T15 | `tests/unit/executor/test_executor.py::TestTheKillSwitchIsCheckedInside::test_calling_execute_directly_still_refuses`<br>`tests/e2e/drills/test_kill_switch.py::test_a_credit_approved_while_the_switch_is_on_never_reaches_the_ledger`<br>`tests/e2e/drills/test_security_gaps.py::TestT15AnUnreadableSwitchIsOn::test_a_switch_that_cannot_be_read_counts_as_engaged` | the switch is checked inside the executor; nothing reaches the ledger; unreadable counts as on |
| T16 | `tests/unit/modules/email/test_poller.py::TestRouting::test_two_matching_cases_are_unroutable`<br>`tests/e2e/test_inbound_review_fixes.py::TestM2RoutingIsExact::test_a_reference_matches_only_as_a_whole_token` | ambiguous replies are unroutable; references match whole |
| T17 | `tests/unit/orchestrator/test_steps.py::TestAssessChecksI6First::test_a_required_item_on_a_dead_module_escalates_with_its_name`<br>`tests/e2e/test_inbound_review_fixes.py::TestM1HeldEventsAreRedeliveredAfterAnyMove::test_a_reply_held_during_approval_is_handled_once_the_case_moves`<br>`tests/e2e/drills/test_restart.py::test_awaiting_seller_the_reminder_still_comes_once` | a dead module is named; held events are redelivered; restarts lose nothing |
| T18 | `tests/unit/modules/approvals/test_review_console.py::TestTheApprovalIsBoundToTheText::test_the_console_never_re_renders_the_text` | partial: the console shows exactly the approved text (see gaps) |
| T19 | `tests/unit/orchestrator/test_intake.py::TestTheRecipientAllowlist::test_an_unlisted_seller_is_refused`<br>`tests/unit/modules/evidence/test_generator.py::TestTheWorldIsSynthetic::test_every_seller_is_on_a_test_domain` | unlisted sellers are refused; all data is synthetic |
| T20 | `tests/unit/policy/test_rules.py::TestCautionSignalsOnlyRaise::test_a_signal_cannot_lower_a_deny`<br>`tests/unit/modules/reasoning/test_two_pass.py::TestRelaxationIsBlocked::test_the_result_is_never_less_cautious_than_pass_one` | patterns only add caution (Phase 7 completes this) |
| T21 | `tests/unit/infra/test_schema_constraints.py::TestCases::test_an_illegal_state_change_is_refused`<br>`tests/unit/infra/test_schema_constraints.py::TestEventsAreAppendOnly::test_an_event_cannot_be_updated` | the database refuses illegal moves and edits to the trail |
| T22 | `tests/unit/modules/email/test_token_cache.py::TestTheCacheIsEncryptedOrAbsent::test_no_plain_text_fallback_is_taken` | the token cache is encrypted or absent, never plain text |

## Closed by this review

- **T03** - a reply that failed SPF/DKIM/DMARC was parsed but never reached the case. It is now
  carried on the reply (`sender_auth`), recorded as evidence, and raises an ESCALATE caution
  signal (`sender_unauthenticated`).
- **T09** - `approvals.approver_ids` was configured and never read. A decision by a person not on
  the list now sends the case to a person and moves nothing; the API binds to 127.0.0.1 only
  (`make run-api`), and the open `/inbound` endpoint refuses people's decisions.
- **T15** - an unreadable kill switch counting as on is now tested (and implemented in the new
  `SqliteKillSwitch`).
- **T04** - a recipient changed on read-back is now tested to end UNKNOWN, never verified.
- A verdict raised by a caution signal now names the signal's reason (it showed "ESCALATE").

## Checklist

- **Least privilege (O5).** Outlook: delegated `Mail.ReadWrite`, `Mail.Send`, `Mail.Read` on one
  dedicated pilot mailbox (`integrations.md` 1); no application permissions. Jira: an API token for
  a test account on one test project. Both need the owner's accounts and are not in the repo.
- **Log redaction** end to end: T10's tests; error logs now record exception types only (ADR 0033).
- **Prompt injection by every route**: `tests/evals/injection/` (statement, reply, note, comment)
  and scenarios S14a, S14b, S22, S40 in `make eval`: 0 of 4 injection runs succeeded.
- **Secret scanning**: `tests/architecture/test_no_secrets.py` runs in `make verify`.

## Remaining gaps (accepted for the pilot)

- **T18** - the review screen's order (consequence, exact payload, code facts first) and the
  labelling of AI and seller text are not tested; only that the approved text is shown unchanged.
- **T09** - the API has no authentication; it is safe only because it listens on loopback and
  refuses people's decisions. A real deployment needs sign-in.
- **T05** - the approval-decision dedupe key is exercised but not asserted by name.
- **T20** - "outcomes decided with pattern help never confirm a pattern" is Phase 7's.

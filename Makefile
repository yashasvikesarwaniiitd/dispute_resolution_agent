# Every target delegates to scripts/make.py, the single implementation (card P0-02).
# GNU Make is not available on every machine this project is built on; make.cmd and
# make.sh give the same target names there. CLAUDE.md section 15 defines the list.

PY := uv run python scripts/make.py

.PHONY: setup verify test test-integration lint typecheck arch scenarios run-api run-approvals email-login demo eval agents-sync foundry-check try-agent

setup:
	$(PY) setup

verify:
	$(PY) verify

test:
	$(PY) test

test-integration:
	$(PY) test-integration

lint:
	$(PY) lint

typecheck:
	$(PY) typecheck

arch:
	$(PY) arch

scenarios:
	$(PY) scenarios

run-api:
	$(PY) run-api

run-approvals:
	$(PY) run-approvals

email-login:
	$(PY) email-login

demo:
	$(PY) demo

eval:
	$(PY) eval

agents-sync:
	$(PY) agents-sync

foundry-check:
	$(PY) foundry-check

# make try-agent ARGS="injection_in_statement --offline"
try-agent:
	$(PY) try-agent $(ARGS)

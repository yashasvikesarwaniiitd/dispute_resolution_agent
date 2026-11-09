# Every target delegates to scripts/make.py, the single implementation (card P0-02).
# GNU Make is not available on every machine this project is built on; make.cmd and
# make.sh give the same target names there. CLAUDE.md section 15 defines the list.

PY := uv run python scripts/make.py

.PHONY: setup verify test test-integration lint typecheck arch scenarios run-api run-approvals email-login gmail-login explain demo eval agents-sync foundry-check foundry-login foundry-spend llm-check try-agent red-team eval-image

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

run-console:
	$(PY) run-console

email-login:
	$(PY) email-login

hosted-secrets:
	$(PY) hosted-secrets

gmail-export:
	$(PY) gmail-export

gmail-login:
	$(PY) gmail-login

# make explain ARGS="<case_id> <question>"  |  ARGS="--list"  |  ARGS="--check"
explain:
	$(PY) explain $(ARGS)

# make demo ARGS="--offline calc_error_wrong_slab"
demo:
	$(PY) demo $(ARGS)

eval:
	$(PY) eval

agents-sync:
	$(PY) agents-sync

foundry-check:
	$(PY) foundry-check

# make foundry-login ARGS="--device-code" if the browser redirect is blocked
foundry-login:
	$(PY) foundry-login $(ARGS)

# make try-agent ARGS="injection_in_statement --offline"
try-agent:
	$(PY) try-agent $(ARGS)

foundry-spend:
	$(PY) foundry-spend

llm-check:
	$(PY) llm-check

# make red-team                 the injection corpus through every route, offline (free)
# make red-team ARGS="--live"   against the real Foundry agent (costs; owner approval first)
red-team:
	$(PY) red-team $(ARGS)

# make eval-image               the image check's 20 photos against Foundry gpt-5-mini (ADR 0049;
#                               costs money, capped at US$1 a run; owner approval first)
eval-image:
	$(PY) eval-image $(ARGS)

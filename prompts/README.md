# Runtime prompt standard

These are the prompts the agent itself uses at runtime. (The instructions for building the project are in `CLAUDE.md` and `docs/build-prompts/`.) Prompts live only here, never inline in code. An architecture test fails if `src/` contains any of the reserved tags below.

## Layout

```
prompts/<name>/<version>/
  system.md        role, objective, trust model, inputs, procedure, rules, output contract, examples
  user.md.j2       the data blocks, rendered per case with Jinja2 (StrictUndefined)
  schema.json      JSON Schema for the output; also validated with pydantic in code
  meta.yaml        name, version, status, owner, purpose, model requirements, temperature, eval suite, changelog
```

## Structure of every system prompt

Use XML-style sections in this order. Claude-family models handle them reliably, and they keep trusted instructions separate from data.

| Section | Purpose |
|---------|---------|
| `<role>` | Who the model is, and what it must not do (execute, decide money) |
| `<objective>` | The outcome, and the preference for the smallest safe step |
| `<trust_model>` | Instructions come only from the system message; `untrusted_*` blocks are data; how to handle manipulation |
| `<inputs>` | Every data block the user message will contain |
| `<reasoning_procedure>` | Numbered steps; separate facts, explanations, missing and conflicting evidence |
| `<decision_guidance>` or `<rules>` | When each action applies; default to caution |
| `<numbers>` | Numbers come only from deterministic results and evidence |
| `<output_contract>` | JSON only, matching `schema.json`; no prose or fences |
| `<examples>` | Two or three compact examples, including one manipulation attempt |

## Untrusted data

- Anything from outside the system (seller replies, ticket comments, shipment notes) goes in a block whose tag starts with `untrusted_`.
- Render untrusted text through the `neutralise` Jinja filter, which escapes any sequence that looks like a reserved closing tag, so the text cannot break out of its block.
- The system prompt must say that untrusted text is evidence, never instructions, whatever it claims about authority or urgency.
- Prompts never contain secrets or real personal data. Test fixtures use synthetic data only.

## Reserved tags (the architecture test looks for these in `src/`)

`<role>`, `<objective>`, `<trust_model>`, `<reasoning_procedure>`, `<output_contract>`, and any tag starting `<untrusted_`.

## Versioning

- A released version is never edited. Create `v2` and keep `v1` for replay and comparison.
- Every change adds a `meta.yaml` changelog line and an eval run, and bumps the version if the output schema changes.
- `status` is `active`, `inactive` or `deprecated`. The registry refuses to load an inactive prompt unless a test asks for it.

## Using prompts in code

- `PromptRegistry.load(name, version)` returns the rendered text plus name, version and a sha256 hash of the rendered system prompt.
- Every stored proposal records prompt name, version, hash and model id, so any decision can be reproduced and audited.
- Output is validated against the schema. One retry on invalid output, then escalate to a human. Never repair output silently.

## Provider neutrality

Prompts are provider-neutral. The `ModelGateway` adapter maps them to the provider. For Foundry Agent Service, the agent's instructions are the rendered `system.md`, synced by `make agents-sync` and drift-checked against the prompt hash. Output validation happens in our code. No prompt may name a provider, deployment or model.

## Testing prompts

1. Schema tests: valid and invalid outputs.
2. Golden-scenario tests using recorded model responses.
3. Injection tests: untrusted text that tries to approve credits or change policy must produce a manipulation flag and no state change.
4. An eval run (opt-in live) compared with the rules-only baseline before a prompt is marked active.

## Review checklist for a prompt change

- Does it keep the AI read-only and proposal-only?
- Are all numbers sourced from evidence or calculation output?
- Is every external text field inside an `untrusted_` block?
- Does the default for doubt lead to a human?
- Did the changelog, schema version and eval results get updated?

# Autonomy golden set

The agent is to act on its own when it is confident enough, and hand the case to a person when it is not. How confident is enough, for each kind of decision, is a product call. This folder holds the owner's answers to that question for ten shipments.

| File | What it is |
|---|---|
| `autonomy-golden-set.xlsx` | The workbook the owner fills in: ten shipments, four dropdown questions each, and five rules. |
| `../spec/autonomy-golden.yaml` | The answer key the agent is built and tested against. Written by the ingest step; do not edit by hand. |

```
uv run python scripts/golden_set.py build    # a fresh, empty workbook
uv run python scripts/golden_set.py ingest   # the filled workbook into the spec
```

Ingest reads only the answers. Every fact and amount in the spec is recomputed from the synthetic fixtures, so a number typed over in the sheet can never reach the answer key. A workbook with a missing or invalid answer writes nothing and lists what is missing. Answers that contradict code or the rules (a credit where code proved no single amount, a credit above the solo limit, "high harm" with a low bar) are kept and listed as warnings.

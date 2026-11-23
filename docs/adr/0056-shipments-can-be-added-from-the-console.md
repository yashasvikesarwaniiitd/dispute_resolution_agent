# 0056 — Shipments can be added from the console

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "add a new shipment feature wherein from the UI I can add a new shipment ... the photo is not mandatory"; plan approved: "Yes, build it"). Builds on ADR 0044 (the console), ADR 0049 (parcel photos) and ADR 0050 (the rate card on screen).

## The problem

Every shipment was one of fourteen fixtures in code. Trying a new kind of case, such as a different zone, a bigger overcharge or a parcel with no photo, meant editing `fixtures.py`. The owner wants to add a shipment from the screen, with the important details and an optional photo.

## Decision

1. **A store for added shipments** (`modules/evidence/synthetic_shipments/added.py`): one SQLite file, `shipments.sqlite3`, beside the case database. Each row is what the form sent; the library builds a `Fixture` from it, so an added shipment is served by the same source, with the same three answers and no fourth. The library reads the store on every lookup, so a shipment added while the service runs is served at once. The fixtures win on a shared reference, and the built-in references cannot be added.
2. **Code sets every amount.** The form gives sizes, weights, a zone and a status. The rate card works out the correct charge. To try a billing mistake, a person may type a different charge or tick "charged twice"; code still proves the error and sets any credit (I9).
3. **Nothing is invented.** A field left empty stays MISSING: no photo note means no `shipment.image_description`, no photo means none for the image check. Added shipments have no history and no hub finding until the hub answers.
4. **Saving order:** check the photo, store the shipment, post its charge to the ledger the way start-up seeds the built-in shipments (`seed_debit`, idempotent on the transaction id). A refused photo stores nothing. Start-up seeding also covers added shipments, so a rebuilt ledger still has their charges.
5. **Photos** are kept as `data/photos/<ref>.jpg|png`, typed by their first bytes, at most 5 MB. The image check's `PhotoFolder` and the screens look there after the built-in folder.
6. **Guards:** sides above 0 and at most 300 cm; weights 1 to 100,000 g; a typed charge ₹1 to ₹1,00,000; a photo note at most 300 characters (outside text, I5); at most 50 added shipments. Each row is re-checked on read, so a row edited outside the console is not served. An added shipment gets its own SKU, so it never joins a fixture's cross-case cluster by SKU.
7. **The form** is a dialog on Raise a ticket. Before anything is saved it shows code's preview: what the seller expects, the correct charge, what will be charged, and what code finds ("One error: ₹38.00 more than the rate card gives"). It states code's finding, never the agent's decision.

## Why

Adding to the evidence module's synthetic world, rather than to the kernel, keeps the agent unchanged: it cannot tell an added shipment from a fixture, which is what makes a test on one mean something. Writing directly to the store and the ledger file, as start-up seeding does, avoids a new API surface for what is synthetic test data.

## Options considered

- **A `POST /shipments` endpoint.** Consistent with how tickets are raised, but it is a write API for test data on a public demo, with its own auth question. Rejected for now; the store is the one place to change if it is wanted later.
- **Append to `fixtures.py`.** Needs a code change and a redeploy per shipment. Rejected.

## Consequences

- On the hosted app, storage resets when the app restarts, so added shipments, their photos and their charges disappear then, as cases already do. Keeping them would need an external database.
- The vision image check, when switched on, reads an uploaded photo like a built-in one, so it is counted against the same spending caps.

## Sources

None beyond the repository.

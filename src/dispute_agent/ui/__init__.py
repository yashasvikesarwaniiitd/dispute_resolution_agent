"""The operations console (ADR 0044): four screens over the running system.

Seller tickets, the AI's step-by-step trail, the reviewer's queue and the finance ledger.
A shell like `api/`: it reads the stores and posts disputes to the running API, and nothing
in the system imports it. Every decision these screens show is made elsewhere; the logic
here only arranges what is recorded, and lives in plain Python so it is tested without a
browser. The Streamlit files under `screens/` are layout.
"""

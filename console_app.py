"""`make run-console`: the operations console on this machine (ADR 0044), with the Review
screen's assistant composed in (ADR 0058).

At the repository root, like `streamlit_app.py`, because it composes the app: nothing inside
`dispute_agent` imports `dispute_agent.app` (tests/architecture/test_composition_root.py).
"""

if __name__ == "__main__":
    from streamlit_app import explain

    from dispute_agent.ui.app import main

    main(explain=explain)

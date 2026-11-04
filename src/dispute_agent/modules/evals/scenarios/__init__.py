"""The scenario simulator's pure parts: the catalogue, its scripts and its expectations (P6-01).

Everything here reads and compares; nothing here runs a case. The runner that plays a script
on the whole line is assembled in `api/scenario_runner.py`, because it needs the fakes, the
ledger and the line - which a module may not import.
"""

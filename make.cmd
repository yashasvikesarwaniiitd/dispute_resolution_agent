@echo off
rem Windows shim so `make <target>` works without GNU Make (card P0-02).
uv run python "%~dp0scripts\make.py" %*

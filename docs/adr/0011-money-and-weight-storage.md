# ADR 0011: Money as integer paise and weight as integer grams in storage; no rounding in the kernel

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
In Python, money is `Decimal` INR with exactly two places and weight is `Decimal` kg with exactly three; floats and extra places are validation errors. In SQLite, money is stored as INTEGER paise and weight as INTEGER grams. The kernel never rounds; any rounding is an explicit domain-pack rule (A2 money-rules.md).

## Why
SQLite has no decimal type: a REAL column stores binary floating point and can silently change amounts. Integers are exact, sort correctly and make equality checks (approved amount equals posted amount, I3) trivial. Refusing extra places instead of rounding means a rounding decision can never happen by accident.

## Options considered
- TEXT decimals: exact, but sums and comparisons in SQL become string operations.
- REAL: inexact; ruled out by CLAUDE.md section 7.

## Consequences
Easier: exact arithmetic everywhere. Harder: conversion at the repository boundary (one helper each way, with a property-tested round trip).

## Sources
Project documents only (CLAUDE.md section 7). No external documentation was needed.

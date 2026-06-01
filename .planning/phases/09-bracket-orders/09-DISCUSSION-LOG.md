# Phase 9: Bracket Orders - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the analysis.

**Date:** 2026-06-01
**Phase:** 09-bracket-orders
**Mode:** default (pre-loaded from memory note — no interactive Q&A needed)

## Context Source

Decisions were pre-loaded from the `project-phase9-plan` memory note written in a prior session.
That note contained:
- Confirmed real alert formats (structured summary block + prose variant)
- Complete ibapi bracket mechanics (transmit=False pattern, OCA type, order ID reservation)
- Explicit list of key decisions to resolve during discuss phase
- Scope of changes across parser / schema / executor

No interactive questioning was required because all gray areas were already resolved in the
prior session and documented in the memory note.

## Gray Areas Resolved

| Area | Decision | Rationale |
|------|----------|-----------|
| OCA type | Type 2 (reduce, don't cancel) | Partial TP fills are fine; cancelling all other TPs on one fill is too aggressive |
| TP quantity split | Equal split, remainder to last order | Simplest correct approach; configurable weighting deferred |
| Fallback when quantity < N TPs | Use bare MKT, log warning | 0-share orders rejected by IBKR; no bracket better than broken bracket |
| Stop loss order type | STP (not STP LMT) | Simpler, guaranteed execution |
| transmit flag | False on all except last child | Standard ibapi bracket pattern — must be exact |
| OCA group name | `TP_{parent_order_id}` | Unique, human-readable in TWS |

## Deferred Ideas

- Configurable TP quantity weighting — future enhancement
- Trailing stop — future phase
- Dashboard display of bracket chains — Phase 7 or later
- Modifying/cancelling exits on signal reversal — complex, future phase

"""
bravos/runtime.py — process-wide mutable runtime state (IMPROVEMENTS item 2).

Single home for state shared across threads within one process:
  - ibkr_paused / paused_signal_queue: SIGUSR1/USR2 pause-resume state.
    Written only by scripts/run_ingestion.py signal handlers (main thread);
    read on the alert-socket thread by the scraper. queue.Queue is
    thread-safe; the bool is a single-word read — no lock needed.
  - ibapp: the IBApp singleton. Written by the entry point that owns the
    connection (run_ingestion main/handle_resume, execute_pending,
    validate_pipeline); read at CALL time via `runtime.ibapp` — never via a
    top-level `from`-import of this attribute, which would freeze a stale
    binding.

This module must remain a stdlib-only leaf: library code (scraper,
executor) imports it, so importing anything from bravos/ or scripts/
here would recreate the import-cycle / inversion this module exists
to remove.
"""
import queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bravos.broker.connection import IBApp

ibkr_paused: bool = False
paused_signal_queue: "queue.Queue[int]" = queue.Queue()
ibapp: "IBApp | None" = None

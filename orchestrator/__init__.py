"""Loreweave Studio orchestrator — FastAPI service on 127.0.0.1 (R101).

P0/M0: app factory + health handshake only. Later milestones add workspace I/O
(M5), the persistent job queue (M4), pipeline adapters (M1/M3), the disk guard
(M6) and the launch/component manifest gate (M7).
"""

__version__ = "0.0.1"  # app version (presence-only, not pinned at launch — R97)
SCHEMA_VERSION = 1  # bundle record schema_version (R-data); bump on record changes

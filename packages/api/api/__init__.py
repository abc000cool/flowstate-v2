"""FlowState v2 service layer (CLAUDE.md §8).

FastAPI app + SQLite metadata store + RQ job layer. All long work (simulation
runs, sweeps, calibrations, reports) goes through the job queue; no endpoint
executes a simulation synchronously when the queue backend is Redis. Results
payloads live on disk as Parquet/JSON per docs/CONTRACTS.md §3 — SQLite holds
metadata only.
"""

__version__ = "2.0.0-dev"

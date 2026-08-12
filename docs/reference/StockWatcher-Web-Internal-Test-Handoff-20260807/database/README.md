# Database

`007_web_internal_test.sql` is the reviewed v6-to-v7 reference contract. The application must integrate equivalent statements into the Python migration runner, create a backup before migration, and validate schema, foreign keys and integrity. Do not tell an operator to paste this SQL into the live database as the normal upgrade path.

`008_command_event_transitions.sql` is the additive v7-to-v8 reference contract. It keeps source deduplication for snapshots and alerts while allowing every `command.updated` status transition to be persisted. The same backup, Python-runner and post-migration integrity rules apply.

`009_candidate_outcomes.sql` is the additive v8-to-v9 reference contract for the read-only next-day outcome sidecar. It adds bounded retry state without changing candidate, ranking, fixed-alert or strong-movement tables. Existing accounts, sessions, commands and events must survive unchanged; migration is still run only by `SQLiteStore` after a verified SQLite backup.

# Database

`007_web_internal_test.sql` is the reviewed v6-to-v7 reference contract. The application must integrate equivalent statements into the Python migration runner, create a backup before migration, and validate schema, foreign keys and integrity. Do not tell an operator to paste this SQL into the live database as the normal upgrade path.

`008_command_event_transitions.sql` is the additive v7-to-v8 reference contract. It keeps source deduplication for snapshots and alerts while allowing every `command.updated` status transition to be persisted. The same backup, Python-runner and post-migration integrity rules apply.

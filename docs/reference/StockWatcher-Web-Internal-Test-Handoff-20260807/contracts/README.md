# Contracts

- `openapi.yaml`: authenticated REST API contract.
- `websocket-events.schema.json`: durable WebSocket event envelope and payload variants.
- `command-types.schema.json`: command creation schema; Token commands reference an encrypted `secret_request_id` and never carry plaintext.

The implementation must run contract tests. Changes require a version bump, migration/compatibility analysis and updated clients/tests.

# Configuration registry

Every configurable value (ports, URLs, keys, model aliases, thresholds, counts, paths) must have one row here, and this file must be updated in every step that adds or changes one.

| Name | Purpose | Default | Where set | Example alternative |
|---|---|---|---|---|
| POSTGRES_USER | Postgres superuser name | shopease | .env | copilot_admin |
| POSTGRES_PASSWORD | Postgres password (required, no default; Compose fails if unset) | none, `.env` only | .env | a long random string |
| POSTGRES_DB | Database created on first start | shopease | .env | shopease_dev |
| POSTGRES_PORT | Host port mapped to Postgres (bound to 127.0.0.1) | 5432 | .env | 5433 |
| REDIS_PORT | Host port mapped to Redis (bound to 127.0.0.1) | 6379 | .env | 6380 |

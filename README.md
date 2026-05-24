restdb.io compatibility proxy

FastAPI-based proxy that implements a subset of restdb.io REST behavior used by the project.

Backends: MariaDB (via SQLAlchemy + aiomysql)

Run (development):

```bash
# install deps
python -m pip install -r requirements.txt
# run (assumes MariaDB on localhost:3306)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888
```

Environment: copy `.env.example` to `.env` and set MariaDB connection details.

Linux: Quick install and run
 - Copy `.env.example` to `.env` and edit values as needed.
 - Create a virtualenv, install deps, and run the server:

```bash
cd restdb.io-proxy
./run.sh
```

`run.sh` creates a `.venv` in the workspace, installs dependencies and starts the app (development). To run without creating the venv again, use:

```bash
cd restdb.io-proxy
./start.sh
```

Systemd (example)

Create a unit at `/etc/systemd/system/restdb-io-proxy.service` using the template in `systemd/restdb-io-proxy.service`, then enable and start the service:

```bash
sudo cp restdb.io-proxy/systemd/restdb-io-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now restdb-io-proxy.service
```

Adjust `WorkingDirectory` and `EnvironmentFile` in the unit to match your installation paths.

Docker (recommended for remote or reproducible deployment)

Build and start the proxy with MariaDB and phpMyAdmin using Docker Compose:

```bash
cd restdb.io-proxy
docker compose up --build -d
```

This will build the `proxy` image and start `mariadb`, `phpmyadmin`, and `proxy` services. The `.env` file (if present) will be loaded into the `proxy` container via `env_file`.

Environment variables

You can configure the proxy with environment variables. Copy `.env.example` to `.env` and set the values, or pass variables directly to `docker compose`.

Important variables (see `.env.example`):
- `PROXY_API_KEY` - optional API key to require clients supply `x-apikey` header.
- `DATABASE_URL` - optional full database URL (overrides host/port/user/password/db).
- `DB_USER` - MariaDB username (default: `restdb_user` when using compose).
- `DB_PASSWORD` - MariaDB password (set in `.env` or external secret store).
- `DB_HOST` - MariaDB hostname (default: `mariadb` when using compose).
- `DB_PORT` - MariaDB port (default: `3306`).
- `DB_NAME` - MariaDB database name (default: `restdb_proxy`).

Examples

Use a `.env` file (recommended):

```bash
cp .env.example .env
# edit .env to set PROXY_API_KEY or database credentials
docker compose up --build -d
```

Pass variables inline:

```bash
PROXY_API_KEY=secret docker compose up --build -d
```

phpMyAdmin (GUI for database management)

When using `docker compose up`, phpMyAdmin is automatically started and accessible at:
**http://localhost:8081**

Default credentials (set in docker-compose.yml if not overridden by environment variables):
- Server: `mariadb`
- Username: `root`
- Password: set via `PMA_PASSWORD`

Use this GUI to:
- Browse all tables (queue and requests)
- Create, edit, or delete data manually
- Run raw SQL queries
- Monitor database operations

Health check

The proxy provides a `/health` endpoint (no API key required) to check database connectivity:

```bash
curl http://localhost:8888/health
```

Response (success):
```json
{"status":"ok","redis":"connected"}
```

Response (error):
```json
{"status":"error","redis":"failed","error":"connection error details"}
```

Use this endpoint in monitoring/alerting systems.
restdb.io API Compatibility

The proxy implements a high-level of compatibility with restdb.io REST API. Supported endpoints and features:

**Supported HTTP Methods**
- GET - retrieve documents
- POST - create documents
- PUT - replace entire document
- PATCH - partial update
- DELETE - delete documents

**Collections**
- `/rest/queue` - Generic REST endpoint for queue collection
- `/rest/requests` - Generic REST endpoint for requests collection
- `/rest/config` - Generic REST endpoint for config collection (key/value settings)
- Legacy endpoints `/queue`, `/requests` supported for backward compatibility

**MongoDB-like Queries** (via `?q={}` parameter)
- `$eq` - equals
- `$gt` - greater than
- `$lt` - less than
- `$gte` - greater than or equal
- `$lte` - less than or equal
- `$in` - in array
- `$nin` - not in array
- `$ne` - not equal

Example:
```bash
curl -H "x-apikey: secret" \
  "http://localhost:8888/rest/queue?q={\"priority\":true}"
```

**Header Options** (via `?h={}` parameter)
- `$orderby` - sort fields: `{"priority": 1, "id": -1}` (1=asc, -1=desc)
- `$fields` - select fields: `{"videoId": 1, "priority": 1}`
- `$max` - limit results: `{"$max": 10}`
- `$skip` - offset results: `{"$skip": 5}`

Example:
```bash
curl -H "x-apikey: secret" \
  "http://localhost:8888/rest/queue?h={\"$orderby\":{\"id\":-1},\"$max\":10}"
```

**Metadata API**
- `GET /rest/_meta` - Get database metadata
- `GET /rest/<collection>/_meta` - Get collection metadata (field types, document count)

**Bulk Operations**
- `DELETE /rest/<collection>/*` - Delete by ID list (body: `["id1", "id2"]`)
- `DELETE /rest/<collection>/*?q={...}` - Delete by MongoDB query

**API Key**
- Pass API key via `x-apikey` header
- Set `PROXY_API_KEY` environment variable to enforce authentication

Troubleshooting

**Docker Compose: "Can't connect to MySQL server on 'localhost'"**

This error occurs when the proxy starts before MariaDB is ready. The issue has been fixed with:
- Healthcheck added to MariaDB service
- `depends_on` condition set to `service_healthy`

If you still see this error:
```bash
# Restart all services
docker compose down
docker compose up --build -d

# Check MariaDB is healthy
docker compose ps

# Wait for MariaDB to be ready (may take 15-30 seconds on first start)
docker compose logs mariadb | tail -20

# Restart proxy once MariaDB is healthy
docker compose restart proxy
```

**"Can't initialize database" warnings in logs**

The proxy now continues operation even if the database is not ready at startup. Tables will be created on first request.

**phpMyAdmin: Can't connect to database**

If phpMyAdmin cannot connect, ensure:
1. MariaDB service is running: `docker compose ps`
2. Check MariaDB logs: `docker compose logs mariadb`
3. Verify environment variables match between docker-compose.yml and MariaDB container

**Checking database connectivity**

Use the health endpoint:
```bash
curl http://localhost:8888/health
```

Or test MariaDB directly:
```bash
docker exec restdb.io-proxy-mariadb-1 mysqladmin -h localhost -u root -p${MYSQL_ROOT_PASSWORD} ping
```

**Logs**

View all service logs:
```bash
docker compose logs -f
```

View specific service:
```bash
docker compose logs -f proxy
docker compose logs -f mariadb
```
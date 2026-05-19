# Server deploy (Ubuntu 22.04)

Assumes the repo lives at **`/home/ovehbe/islamqa`** with `data/islamqa.db`, `.env` (`OPENAI_API_KEY=...`), and DNS pointing at this host.

## 1. System packages

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx sqlite3 curl git build-essential
```

## 2. Python environment (uv + project venv)

Ubuntu 22.04’s system Python is too old; `uv` installs the version from `.python-version` (3.14) into the project venv.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# reopen shell or: source $HOME/.local/bin/env

cd /home/ovehbe/islamqa
uv sync
```

Smoke test (Ctrl+C after you see it listening):

```bash
cd /home/ovehbe/islamqa
set -a && source .env && set +a
.venv/bin/islamqa-mcp --config config.yml --transport streamable-http
# curl -s http://127.0.0.1:8000/api/stats | head
```

Optional in `.env`:

```bash
ISLAMQA_SEARCH_APP_URL=https://search.islamqa-mcp.org
```

## 3. systemd — MCP service

```bash
sudo cp /home/ovehbe/islamqa/deploy/islamqa-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now islamqa-mcp
sudo systemctl status islamqa-mcp
journalctl -u islamqa-mcp -f
```

## 4. systemd — scrape / embed / rebuild timer

The oneshot runs **incrementally** (scrape skips known IDs; embed skips rows with vectors; `build_db` merges; then restarts MCP).

Allow the scrape job to restart MCP without a password:

```bash
echo 'ovehbe ALL=(ALL) NOPASSWD: /bin/systemctl restart islamqa-mcp' | sudo tee /etc/sudoers.d/islamqa-mcp-restart
sudo chmod 440 /etc/sudoers.d/islamqa-mcp-restart
```

Install timer + oneshot:

```bash
sudo cp /home/ovehbe/islamqa/deploy/islamqa-scrape.service /etc/systemd/system/
sudo cp /home/ovehbe/islamqa/deploy/islamqa-scrape.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now islamqa-scrape.timer
systemctl list-timers | grep islamqa
```

Run pipeline once manually:

```bash
sudo systemctl start islamqa-scrape.service
journalctl -u islamqa-scrape -e
```

**Timer schedule** (default: 04:00 and 16:00 server local time). To run once daily at 03:00, edit the timer:

```ini
OnCalendar=*-*-* 03:00:00
```

For every 6 hours:

```ini
OnCalendar=*-*-* 00,06,12,18:00:00
```

Then: `sudo systemctl daemon-reload && sudo systemctl restart islamqa-scrape.timer`

## 5. nginx + TLS

```bash
sudo cp /home/ovehbe/islamqa/deploy/nginx.conf /etc/nginx/sites-available/islamqa-mcp
sudo ln -sf /etc/nginx/sites-available/islamqa-mcp /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

Issue certificates (all hostnames in `deploy/nginx.conf`):

```bash
sudo certbot --nginx -d islamqa-mcp.org -d www.islamqa-mcp.org \
  -d search.islamqa-mcp.org -d api.islamqa-mcp.org
sudo systemctl reload nginx
```

Verify:

```bash
curl -s https://api.islamqa-mcp.org/api/stats
curl -s 'https://search.islamqa-mcp.org/api/search?q=prayer&limit=3' | head -c 500
```

## 6. MCP clients

Everything lives on one domain — same pattern as hadith-mcp:

- **Landing page:** `https://islamqa-mcp.org/` (static `site/`)
- **MCP endpoint:** `https://islamqa-mcp.org/` (root path via `FASTMCP_STREAMABLE_HTTP_PATH=/`)
- **REST API:** `https://islamqa-mcp.org/api/*` (also proxied)
- **Search UI:** `https://search.islamqa-mcp.org/` (static `search/`)

In Cursor / Claude Desktop, point the MCP client at `https://islamqa-mcp.org` (streamable HTTP).

## Paths

Deploy units use `/home/ovehbe/islamqa`. If you prefer `/var/www/islamqa`, symlink:

```bash
sudo mkdir -p /var/www
sudo ln -s /home/ovehbe/islamqa /var/www/islamqa
```

…and change paths in the unit files, or keep home path as-is (nginx `root` already points at `/home/ovehbe/islamqa/site` and `search` in `nginx.conf` — update those `root` lines if you move the tree).

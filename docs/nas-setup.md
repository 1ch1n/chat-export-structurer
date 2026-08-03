# Running MyChatArchive on a NAS

Guide for running the MCP server on a NAS (tested on Asustor Flashstor FS6712X) with HTTPS and LAN access from Claude Desktop.

## What this gives you

- MCP server running 24/7 on your NAS, not dependent on your PC being on
- HTTPS with a locally-trusted cert (no cert warnings, no cloud CA)
- Claude Desktop connects via `mcp-remote` bridge
- Auto-starts on NAS boot

## Requirements

- NAS running Linux (Asustor ADM, Synology DSM, etc.)
- Python 3.10+ available on the NAS (via App Central or package manager)
- `mkcert` installed on your Windows/Mac PC
- Node.js + `npx` available on your PC (for `mcp-remote`)

---

## Step 1: Generate a locally-trusted cert (Windows)

```powershell
# Install mkcert (via scoop or winget)
scoop install mkcert

# Install local CA into Windows trust store (one-time)
mkcert -install

# Generate cert for your NAS IP
mkcert 192.168.1.20
# Produces: 192.168.1.20.pem and 192.168.1.20-key.pem
```

Copy both files to your NAS — e.g. `\\192.168.1.20\Knowledgebase\mychatarchive\tls\`.

---

## Step 2: Install mychatarchive on the NAS

SSH into the NAS, then:

```bash
# Copy the project from your PC first (run on PC):
# xcopy /E /I "C:\path\to\mychatarchive-v2" "\\192.168.1.20\share\mychatarchive\app"

cd /volume1/Knowledgebase/mychatarchive/app
pip3 install -e ".[dev]"
pip3 install pysqlite3-binary   # required: ADM's Python lacks enable_load_extension
pip3 install openai             # required if using OpenAI embeddings

# Add to PATH
echo 'export PATH="/volume1/.@plugins/AppCentral/python3/bin:$PATH"' >> ~/.profile
source ~/.profile
```

---

## Step 3: Configure the NAS

Create `~/.mychatarchive/config.json`:

```json
{
  "storage": {
    "backend": "sqlite",
    "path": "/volume1/Knowledgebase/mychatarchive/archive.db"
  },
  "embeddings": {
    "backend": "openai",
    "model": "text-embedding-3-large",
    "dimension": 3072,
    "openai_api_key": "YOUR_KEY_HERE"
  },
  "transport": {
    "type": "sse",
    "port": 8420
  },
  "auto_sources": {
    "claude_code": false,
    "cursor": false
  }
}
```

If using local embeddings (384 dim), set `backend: "local"` and remove the openai fields. Make sure your stored embeddings match the model you configure here.

---

## Step 4: Start the server

```bash
mychatarchive serve \
  --transport sse \
  --port 8420 \
  --ssl-certfile /volume1/Knowledgebase/mychatarchive/tls/192.168.1.20.pem \
  --ssl-keyfile  /volume1/Knowledgebase/mychatarchive/tls/192.168.1.20-key.pem \
  --db /volume1/Knowledgebase/mychatarchive/archive.db
```

Test from your PC:
```powershell
curl.exe --ssl-no-revoke https://192.168.1.20:8420/sse
# Should return: event: endpoint ...
```

---

## Step 5: Auto-start on boot

Create `/volume1/Knowledgebase/mychatarchive/start-mcp.sh`:

```sh
#!/bin/sh
export PATH="/volume1/.@plugins/AppCentral/python3/bin:$PATH"
DB=/volume1/Knowledgebase/mychatarchive/archive.db
CERT=/volume1/Knowledgebase/mychatarchive/tls/192.168.1.20.pem
KEY=/volume1/Knowledgebase/mychatarchive/tls/192.168.1.20-key.pem
LOG=/volume1/Knowledgebase/mychatarchive/mcp-server.log
mychatarchive serve --transport sse --port 8420 --ssl-certfile "$CERT" --ssl-keyfile "$KEY" --db "$DB" >> "$LOG" 2>&1 &
echo $! > /volume1/Knowledgebase/mychatarchive/mychatarchive.pid
```

```bash
chmod +x /volume1/Knowledgebase/mychatarchive/start-mcp.sh
sudo sh -c 'echo "@reboot /volume1/Knowledgebase/mychatarchive/start-mcp.sh" >> /etc/crontab'
```

Stop/start manually:
```bash
kill $(cat /volume1/Knowledgebase/mychatarchive/mychatarchive.pid)
/volume1/Knowledgebase/mychatarchive/start-mcp.sh
```

---

## Step 6: Configure Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mychatarchive": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://192.168.1.20:8420/sse"],
      "env": {
        "NODE_EXTRA_CA_CERTS": "C:\\Users\\YOUR_USERNAME\\AppData\\Local\\mkcert\\rootCA.pem"
      }
    }
  }
}
```

`NODE_EXTRA_CA_CERTS` tells Node.js to trust mkcert's local CA. Find the exact path with:
```powershell
# In PowerShell after installing mkcert:
mkcert -CAROOT
# rootCA.pem is in that directory
```

Restart Claude Desktop. The mychatarchive tools will appear.

---

## Mobile access

To use MyChatArchive from your phone (Claude mobile, away from home):

1. Your NAS has a VPN server built in (Asustor: VPN Server app in App Central)
2. Set up WireGuard on the NAS and export a client config
3. Install WireGuard on your phone and import the config
4. When connected to the VPN, your phone is on your home network and can reach `192.168.1.20:8420`
5. Claude mobile's Connectors feature can then connect to `https://192.168.1.20:8420/sse`

Note: Claude mobile's Connectors require OAuth. On the same home WiFi (no VPN needed), try adding the URL directly in Claude mobile Settings → Connectors.

---

## Upgrading: sensitivity migration (v0.4.0)

v0.4.0 adds a `sensitivity` column to every content table. The migration runs
automatically on first open, takes a verified backup first, and is idempotent —
but on a production NAS archive, run it deliberately rather than letting the
server trigger it mid-request:

```bash
# 1. Stop the server
kill $(cat /volume1/Knowledgebase/mychatarchive/mychatarchive.pid)

# 2. Pull the new version
cd /volume1/Knowledgebase/mychatarchive/app && git pull && pip3 install -e ".[dev]"

# 3. Trigger the migration explicitly (any command that opens the db works;
#    this one also shows you the resulting counts)
mychatarchive classify --list --db /volume1/Knowledgebase/mychatarchive/archive.db

# 4. Verify the automatic backup exists next to the archive
ls -lh /volume1/Knowledgebase/mychatarchive/archive.pre-v3-*.backup.sqlite

# 5. Restart
/volume1/Knowledgebase/mychatarchive/start-mcp.sh
```

Notes:

- The backup is a full copy of the database, written to the same volume —
  make sure there is enough free space (same size as `archive.db`).
- Every existing row defaults to `public`; nothing changes behavior until you
  classify threads.
- Once verified, the `*.backup.sqlite` file can be moved elsewhere or deleted.

---

## Cert renewal

mkcert certs are valid ~2 years. When they expire:

```powershell
mkcert 192.168.1.20
# Copy new .pem files to NAS tls/ folder
```

Then restart the server. The CA never needs to be reinstalled (valid 10 years).

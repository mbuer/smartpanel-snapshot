# NUC Deployment Guide

This guide describes a persistent Linux deployment of **SmartPanel Snapshot + Grafana OSS + Loki + Grafana Alloy** on a utility NUC.

It captures the deployment pattern proven during development. Treat paths, usernames, network ranges, retention periods, and firewall policy as examples and adapt them to the target host.

## Target Architecture

```text
SmartPanels
    │
    │ Live View WebSocket
    ▼
SmartPanel Snapshot
    │
    ├── snapshots/
    ├── metrics/
    │
    └── /var/log/smartpanel/panel-health.jsonl
                         │
                         ▼
                       Alloy
                         │
                         ▼
                        Loki
                         │
                         ▼
                    Grafana OSS
```

The API, Grafana, Loki, and Alloy run directly on the Linux host as systemd services.

## 1. Prepare the Repository

Clone the project:

```bash
git clone https://github.com/mbuer/smartpanel-snapshot.git
cd smartpanel-snapshot
```

Create the Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verify the application before creating services:

```bash
./save_all.sh
./check_all.sh
```

Review `config.yaml` and adapt the SmartPanel subnet and paths to the deployment.

## 2. Prepare the SmartPanel Log Directory

`compare.py` writes the Alloy/Loki feed to the path configured under:

```yaml
logging:
  alloy_log: "/var/log/smartpanel/panel-health.jsonl"
```

A normal user cannot create arbitrary directories under `/var/log`, so create the directory once with elevated privileges.

Replace `<user>` with the account that runs SmartPanel Snapshot:

```bash
sudo mkdir -p /var/log/smartpanel
sudo chown <user>:<user> /var/log/smartpanel
```

Run a check:

```bash
./check_all.sh
```

Verify:

```bash
ls -lh /var/log/smartpanel/
tail -5 /var/log/smartpanel/panel-health.jsonl
```

You should see one JSON line for every successful panel comparison.

> The application can create the JSONL file once the parent directory is writable, but creating `/var/log/smartpanel` itself is a host-administration task and should be documented rather than hidden inside application code.

## 3. Install Grafana OSS

Use Grafana's supported package repository for the Linux distribution, then enable Grafana:

```bash
sudo systemctl enable --now grafana-server
```

Verify:

```bash
systemctl status grafana-server --no-pager
```

Grafana's default interface is:

```text
http://<server-ip>:3000
```

Complete the initial Grafana login/setup in the browser.

## 4. Install Loki

Install Loki from the Grafana package repository.

Verify the installed version:

```bash
loki --version
```

The repository contains the tested reference configuration:

```text
monitoring/loki/config.yml
```

Review it before deployment. In particular, confirm:

- local filesystem storage is intended
- `path_prefix` uses persistent storage
- chunks/rules are below `/var/lib/loki`
- retention matches the site requirement
- the HTTP port is appropriate

A local single-node deployment can use:

```yaml
common:
  instance_addr: 127.0.0.1
  path_prefix: /var/lib/loki
  storage:
    filesystem:
      chunks_directory: /var/lib/loki/chunks
      rules_directory: /var/lib/loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
```

The tested deployment uses a 30-day retention period:

```yaml
limits_config:
  retention_period: 720h
```

with compactor retention enabled:

```yaml
compactor:
  working_directory: /var/lib/loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
```

### Loki service user

Package installations may create a `loki` user without a matching `loki` group. Check instead of assuming:

```bash
systemctl show loki -p User -p Group
id loki
```

Create persistent storage and assign it to the actual service identity. For example, if Loki runs as user `loki` with primary group `nogroup`:

```bash
sudo mkdir -p /var/lib/loki
sudo chown -R loki:nogroup /var/lib/loki
```

Do not blindly use `loki:loki` unless that group exists.

Install/review the configuration:

```bash
sudo cp monitoring/loki/config.yml /etc/loki/config.yml
```

Then:

```bash
sudo systemctl restart loki
sudo systemctl enable loki
```

Check:

```bash
systemctl status loki --no-pager
curl http://localhost:3100/ready
```

### Loki startup behavior

Immediately after startup, `/ready` may temporarily return messages such as:

```text
Ingester not ready: waiting for 15s after being ready
```

or:

```text
Pattern Ingester not ready: waiting for 15s after being ready
```

This can be normal during initialization. Recheck after the components have settled.

The desired response is:

```text
ready
```

Confirm persistent directories:

```bash
sudo find /var/lib/loki -maxdepth 2 -type d
```

Do **not** use `/tmp/loki` for a persistent deployment because `/tmp` is not appropriate for long-lived observability data.

## 5. Install Grafana Alloy

Install Alloy from the Grafana package repository.

The repository contains the local SmartPanel reference configuration:

```text
monitoring/alloy/config.alloy
```

Its purpose is simple:

```text
/var/log/smartpanel/panel-health.jsonl
                │
                ▼
              Alloy
                │
                ▼
        http://localhost:3100
                │
                ▼
               Loki
```

Install the reviewed config:

```bash
sudo cp monitoring/alloy/config.alloy /etc/alloy/config.alloy
```

Enable Alloy:

```bash
sudo systemctl enable --now alloy
```

Verify:

```bash
systemctl status alloy --no-pager
sudo journalctl -u alloy -n 30 --no-pager
```

A healthy startup should show Alloy loading components and tailing:

```text
/var/log/smartpanel/panel-health.jsonl
```

The local Alloy UI normally listens on:

```text
http://localhost:12345
```

Whether this interface should be reachable remotely is a deployment/security decision.

## 6. Verify SmartPanel → Alloy → Loki

Run:

```bash
./check_all.sh
```

Confirm the JSONL feed changed:

```bash
tail -5 /var/log/smartpanel/panel-health.jsonl
```

Then verify Alloy is still healthy:

```bash
systemctl status alloy --no-pager
sudo journalctl -u alloy -n 30 --no-pager
```

Verify Loki:

```bash
curl http://localhost:3100/ready
```

At this point the data path should be:

```text
check_all.sh
     ↓
compare.py
     ↓
panel-health.jsonl
     ↓
Alloy
     ↓
Loki
```

## 7. Connect Grafana to Loki

Open:

```text
http://<server-ip>:3000
```

In Grafana, add a Loki data source.

When Grafana and Loki run on the same NUC, use:

```text
http://localhost:3100
```

Save and test the data source.

After running `./check_all.sh`, verify that SmartPanel log streams are visible in Grafana Explore before importing the dashboard.

## 8. Import the SmartPanel Dashboard

The repository contains:

```text
dashboards/smartpanel-operations-dashboard.json
```

In Grafana:

1. Open the dashboard import workflow.
2. Upload or paste the dashboard JSON.
3. Select the local Loki data source when requested.
4. Save the dashboard.

Keeping the dashboard JSON in Git makes the dashboard portable between the development VM, the NUC, and future deployments.

If you improve the dashboard in Grafana, export the updated JSON and replace the repository copy deliberately.

## 9. Run the REST API Automatically

For manual testing:

```bash
source .venv/bin/activate
uvicorn smartpanel_api:app --host 0.0.0.0 --port 8081
```

For a permanent NUC, use systemd.

Create:

```text
/etc/systemd/system/smartpanel-api.service
```

Example:

```ini
[Unit]
Description=SmartPanel Snapshot API
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
WorkingDirectory=<project-path>
ExecStart=<project-path>/.venv/bin/uvicorn smartpanel_api:app --host 0.0.0.0 --port 8081
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

For example, `<project-path>` might be:

```text
/home/<user>/python/smartpanel-snapshot
```

The important detail is that `ExecStart` calls Uvicorn **inside the project's virtual environment**. No interactive `source .venv/bin/activate` is required during boot.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smartpanel-api
```

Verify:

```bash
systemctl is-enabled smartpanel-api
systemctl is-active smartpanel-api
systemctl status smartpanel-api --no-pager
```

Expected:

```text
enabled
active
```

Test:

```bash
curl http://localhost:8081/health
```

Logs:

```bash
sudo journalctl -u smartpanel-api -n 50 --no-pager
```

Live logs:

```bash
sudo journalctl -u smartpanel-api -f
```

The API should now return automatically after a NUC reboot.

## 10. Useful Interfaces

Replace `<server-ip>` with the NUC address.

| Service | Address | Purpose |
|---|---|---|
| Grafana | `http://<server-ip>:3000` | Dashboard and log exploration |
| SmartPanel API | `http://<server-ip>:8081` | REST service |
| Swagger API | `http://<server-ip>:8081/docs` | Interactive API |
| API Health | `http://<server-ip>:8081/health` | API health check |
| API Status | `http://<server-ip>:8081/status` | Latest SmartPanel status |
| Loki | `http://localhost:3100` | Local log database |
| Loki readiness | `http://localhost:3100/ready` | Readiness check |
| Alloy | `http://localhost:12345` | Local Alloy interface |

For a local stack, Loki and Alloy generally do not need to be exposed beyond the NUC.

## 11. Full Stack Verification

Run:

```bash
systemctl status smartpanel-api --no-pager
systemctl status grafana-server --no-pager
systemctl status loki --no-pager
systemctl status alloy --no-pager
```

Then:

```bash
curl http://localhost:8081/health
curl http://localhost:3100/ready
```

Generate fresh SmartPanel data:

```bash
cd <project-path>
source .venv/bin/activate
./check_all.sh
```

Inspect the feed:

```bash
tail -5 /var/log/smartpanel/panel-health.jsonl
```

Finally, open Grafana and verify that the new comparison events appear.

## 12. Reboot Test

A persistent deployment is not finished until it survives a reboot.

After an appropriate maintenance window:

```bash
sudo reboot
```

After the host returns:

```bash
systemctl is-active smartpanel-api
systemctl is-active grafana-server
systemctl is-active loki
systemctl is-active alloy

curl http://localhost:8081/health
curl http://localhost:3100/ready
```

Run another SmartPanel check and confirm the new events appear in Grafana.

## 13. Updating the Deployment

Application updates:

```bash
cd <project-path>
git pull
source .venv/bin/activate
pip install -r requirements.txt
```

If Python/API code changed:

```bash
sudo systemctl restart smartpanel-api
```

Repository monitoring configs do **not** automatically replace `/etc` configuration. This is intentional.

Review differences first:

```bash
diff -u /etc/alloy/config.alloy monitoring/alloy/config.alloy
diff -u /etc/loki/config.yml monitoring/loki/config.yml
```

Only deploy changes deliberately, then restart the relevant service.

## 14. Git and Machine-Specific Data

Share through Git:

```text
Python code
shell scripts
README / docs
Grafana dashboard exports
Alloy reference configuration
Loki reference configuration
```

Keep on the host:

```text
/var/log/smartpanel/
/var/lib/loki/
/var/lib/alloy/
credentials
tokens
private SSH keys
other runtime state
```

`config.yaml` contains site-specific values. Review it carefully when using the repository on multiple systems.

## 15. Troubleshooting

### `Permission denied: '/var/log/smartpanel'`

Create the directory and give the application user ownership:

```bash
sudo mkdir -p /var/log/smartpanel
sudo chown <user>:<user> /var/log/smartpanel
```

### Loki says `Pattern Ingester not ready`

Wait for startup initialization and recheck:

```bash
curl http://localhost:3100/ready
```

If it persists, inspect:

```bash
sudo journalctl -u loki -n 100 --no-pager
```

### Alloy is running but no SmartPanel data appears

Check that the source file exists and is changing:

```bash
ls -lh /var/log/smartpanel/panel-health.jsonl
tail -5 /var/log/smartpanel/panel-health.jsonl
```

Then inspect Alloy:

```bash
sudo journalctl -u alloy -n 100 --no-pager
```

Look for the file-tailing component and Loki write errors.

### One or more panels occasionally fail during `check_all.sh`

First run the check again and inspect the comparison output. The checker includes handling for transient Live View responses, but network/device response timing can still matter.

Check the affected panel directly:

```bash
python compare.py snapshots/<panel>.json
```

### Grafana dashboard is empty

Work from the bottom up:

```text
Does check_all.sh succeed?
        ↓
Does panel-health.jsonl contain new events?
        ↓
Is Alloy tailing the file?
        ↓
Is Loki ready?
        ↓
Can Grafana Explore query Loki?
        ↓
Does the dashboard use the correct Loki data source?
```

This is usually faster than changing the dashboard first.

## Security Notes

The current SmartPanel REST API has no authentication or authorization.

Keep the API on a trusted network and do not expose it directly to the public Internet.

Restore endpoints modify SmartPanel state. The experimental normalization endpoint additionally simulates touchscreen interactions and should be treated accordingly.

Also avoid exposing Loki and Alloy management/listening ports unless required.

## Final Deployment State

A healthy persistent NUC should provide:

```text
SmartPanel Snapshot application     ✓
Saved baseline snapshots            ✓
Concurrent health checks            ✓
JSON metrics                        ✓
JSONL health event stream           ✓
Grafana Alloy                       ✓
Persistent local Loki               ✓
Retention policy                    ✓
Grafana OSS                         ✓
Version-controlled dashboard        ✓
FastAPI REST interface              ✓
API autostart via systemd           ✓
```

That gives the project a clean separation:

**Git stores the application and deployment intent. The NUC stores runtime state. Grafana makes the result visible.**

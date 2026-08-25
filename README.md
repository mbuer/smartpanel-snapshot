# SmartPanel Snapshot

> **Copilot-Assisted Code**
>
> This project was developed with the assistance of AI coding tools.
>
> It is not intended for production use. It is an experimental and educational project that explores what can be built with API access, basic programming knowledge, curiosity, and AI-assisted development.

SmartPanel Snapshot is a Python utility for **discovering, saving, monitoring, comparing, and restoring Riedel SmartPanel runtime states** through the SmartPanel Live View WebSocket interface.

What began as a snapshot-and-restore experiment has grown into a small observability stack: SmartPanel state can be checked continuously, written as structured JSON, collected by Grafana Alloy, stored in Loki, and visualized in Grafana.

The project is deliberately simple. The Python application remains useful on its own; Grafana, Loki, and Alloy are optional layers that turn it into a persistent monitoring system.

## What It Does

- Discovers SmartPanels across a configurable IPv4 subnet
- Saves current SmartPanel runtime states as JSON snapshots
- Checks live state against the saved baseline
- Detects missing and unexpected Listen / Call states
- Calculates compliance and COMPLIANT / NON-COMPLIANT status
- Restores missing or unexpected key states
- Verifies SmartPanel identity before restoring
- Checks multiple saved panels concurrently
- Provides an interactive FastAPI REST interface
- Writes machine-readable JSON metrics and JSONL health events
- Feeds SmartPanel health data through Alloy into Loki
- Includes a Grafana dashboard for operational visualization
- Provides experimental key normalization through simulated touchscreen interaction

## Architecture

```text
                         ┌────────────────────────────┐
                         │        SmartPanels         │
                         └─────────────┬──────────────┘
                                       │
                              Live View WebSocket
                                       │
                                       ▼
                         ┌────────────────────────────┐
                         │     SmartPanel Snapshot    │
                         │                            │
                         │ save / check / restore/API │
                         └──────┬─────────┬───────────┘
                                │         │
                    snapshots/  │         │  metrics/
                                │         │
                                │         ▼
                                │   JSON comparison data
                                │
                                ▼
                  /var/log/smartpanel/panel-health.jsonl
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

The monitoring stack can be local, as shown above, or adapted to send data to a remote Loki/Grafana deployment.

## Repository Layout

```text
smartpanel-snapshot/
├── README.md
├── docs/
│   └── NUC_DEPLOYMENT.md
├── dashboards/
│   └── smartpanel-operations-dashboard.json
├── monitoring/
│   ├── alloy/
│   │   └── config.alloy
│   └── loki/
│       └── config.yml
├── snapshots/
├── metrics/
├── save.py
├── compare.py
├── restore.py
├── smartpanel_api.py
├── save_all.sh
├── check_all.sh
├── restore_all.sh
├── config.py
├── config.yaml
└── requirements.txt
```

`monitoring/` contains reference configurations for the local observability stack. Runtime configuration normally lives under `/etc/alloy/` and `/etc/loki/`.

`dashboards/` contains an exportable Grafana dashboard. Runtime snapshots, metrics, logs, credentials, and Loki data should not be treated as repository configuration.

## Quick Start

### 1. Clone and prepare Python

```bash
git clone https://github.com/mbuer/smartpanel-snapshot.git
cd smartpanel-snapshot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure the SmartPanel network

Site-specific settings live in:

```text
config.yaml
```

Example:

```yaml
smartpanels:
  network: "10.85.226.64/26"
  scan_concurrency: 50
  connect_timeout: 3
  response_timeout: 3

paths:
  snapshots: "snapshots"
  metrics: "metrics"

logging:
  alloy_log: "/var/log/smartpanel/panel-health.jsonl"

api:
  host: "0.0.0.0"
  port: 8081
  version: "1.1.0"
```

Network ranges, paths, usernames, retention periods, and other deployment settings should be adapted to the target environment.

### 3. Save a baseline

```bash
./save_all.sh
```

This discovers compatible SmartPanels in the configured subnet and stores snapshots in:

```text
snapshots/
```

SmartPanel custom names should be unique because the custom name is used as the snapshot filename.

### 4. Check the live system

```bash
./check_all.sh
```

The checker compares every saved panel against its current state. Multiple panel checks run concurrently.

Results are written to:

```text
metrics/
```

and, when configured:

```text
/var/log/smartpanel/panel-health.jsonl
```

### 5. Restore drift

```bash
./restore_all.sh
```

The restore process verifies panel identity, determines drift, corrects unexpected/missing states, reads the panel again, and verifies final compliance.

After all restores complete successfully, \`restore_all.sh\` automatically runs a final \`check_all.sh\`. This independently verifies the restored fleet and publishes the latest compliance state to the metrics/JSONL → Alloy → Loki → Grafana pipeline, ensuring the Grafana dashboard immediately reflects the restored state.

## Core Commands

| Task | All saved panels | Individual panel |
|---|---|---|
| Save | `./save_all.sh` | `python save.py <IP-or-CIDR>` |
| Check | `./check_all.sh` | `python compare.py snapshots/<panel>.json` |
| Restore | `./restore_all.sh` | `python restore.py snapshots/<panel>.json` |
| Normalize + Restore | `./restore_all.sh --normalize` | `python restore.py snapshots/<panel>.json --normalize` |

## What Is Compared?

The currently tested SmartPanel operating mode derives active states from Live View LED-ring information:

- **Listen** — upper LED ring green
- **Call / Talk** — lower LED ring red

A comparison reports:

- expected Listen states
- current Listen states
- missing / extra Listen states
- expected Call states
- current Call states
- missing / extra Call states
- total differences
- compliance percentage
- `COMPLIANT` / `NON-COMPLIANT`

Other SmartPanel modes may represent state through different Live View fields and are not yet fully covered.

## REST API

The project includes a FastAPI interface around the main operations.

Start it manually from the project virtual environment:

```bash
uvicorn smartpanel_api:app --host 0.0.0.0 --port 8081
```

For a persistent host, run the API as a systemd service using the virtual environment's Uvicorn executable. See [NUC Deployment](docs/NUC_DEPLOYMENT.md).

Important endpoints:

```text
GET  /health
GET  /status
GET  /config

PUT  /config/network

POST /save
POST /check
POST /restore
POST /restore-normalize
```

The API also allows the discovery network to be changed at runtime. CIDR input is validated and normalized.

### Useful Interfaces

Replace `<server-ip>` with the address of the host running the stack.

| Interface | Address |
|---|---|
| Grafana | `http://<server-ip>:3000` |
| SmartPanel API | `http://<server-ip>:8081` |
| Interactive API / Swagger | `http://<server-ip>:8081/docs` |
| API health | `http://<server-ip>:8081/health` |
| API status | `http://<server-ip>:8081/status` |
| Loki readiness | `http://<server-ip>:3100/ready` |
| Alloy local UI | `http://<server-ip>:12345` |

Loki and Alloy do not need to be exposed outside the host for a local deployment. Bind or firewall these services according to the environment.

## Observability

Each successful comparison can append one structured JSON event to:

```text
/var/log/smartpanel/panel-health.jsonl
```

The local monitoring path is:

```text
compare.py
    │
    ▼
panel-health.jsonl
    │
    ▼
Grafana Alloy
    │
    ▼
Loki
    │
    ▼
Grafana OSS
```

Reference configurations are included in:

```text
monitoring/alloy/config.alloy
monitoring/loki/config.yml
```

The Grafana dashboard export is included in:

```text
dashboards/smartpanel-operations-dashboard.json
```

The monitoring files in this repository are intended as **deployment references**. Review paths, retention, storage, ports, and permissions before installing them on another machine.

For the complete local NUC setup and validation procedure, see:

**[NUC Deployment Guide](docs/NUC_DEPLOYMENT.md)**

## Grafana Dashboard

Import the supplied dashboard into Grafana:

1. Open Grafana at `http://<server-ip>:3000`.
2. Add Loki as a data source if it does not already exist.
3. Use the local Loki URL `http://localhost:3100` when Grafana and Loki run on the same host.
4. Import `dashboards/smartpanel-operations-dashboard.json`.
5. Select the local Loki data source when prompted.

The dashboard is version-controlled so improvements can be shared between deployments rather than recreated manually.

## Experimental Key Normalization

Normalization can be requested before restore:

```bash
./restore_all.sh --normalize
```

or:

```bash
python restore.py snapshots/<panel>.json --normalize
```

> **Experimental feature:** normalization uses simulated touchscreen interactions rather than a dedicated normalization API.

Important limitations:

- touchscreen coordinates are based on the tested SmartPanel layout
- panel-specific behavior is not fully implemented
- simulated touches can occasionally be missed
- different models, firmware versions, or layouts may require different coordinates
- normalization is best-effort rather than deterministic

The REST API deliberately exposes this distinction as a separate `/restore-normalize` operation and labels it experimental in the interactive API documentation.

## Persistent Deployment

A permanent Linux host can run:

```text
smartpanel-api.service
grafana-server.service
loki.service
alloy.service
```

The API service can point directly at:

```text
<project-path>/.venv/bin/uvicorn
```

so a shell does **not** need to activate the Python virtual environment at boot.

A detailed example—including service startup, Loki storage/retention, Alloy configuration, log-directory permissions, dashboard import, and verification—is documented in [docs/NUC_DEPLOYMENT.md](docs/NUC_DEPLOYMENT.md).

## Health Checks

A quick local stack check:

```bash
systemctl status smartpanel-api --no-pager
systemctl status grafana-server --no-pager
systemctl status loki --no-pager
systemctl status alloy --no-pager

curl http://localhost:8081/health
curl http://localhost:3100/ready

tail -5 /var/log/smartpanel/panel-health.jsonl
```

After running:

```bash
./check_all.sh
```

new JSON events should appear in `panel-health.jsonl` and subsequently be queryable in Loki/Grafana.

## Safety

Restore operations **actively modify SmartPanel runtime state**.

Before restoring on an operational system:

- verify that each snapshot belongs to the intended panel
- verify panel IP and identity
- test behavior against the relevant SmartPanel model and firmware
- understand which Live View fields represent the active state
- restrict REST API access to trusted networks

The REST API currently does **not** provide authentication or authorization.

Do not commit:

- Grafana Cloud/API tokens
- passwords or credentials
- private SSH keys
- runtime Loki data
- runtime logs containing sensitive information

## Requirements

Core application:

- Linux
- Python 3
- network access to the SmartPanel management network
- SmartPanels exposing the Live View WebSocket interface
- Python dependencies from `requirements.txt`

Optional observability stack:

- Grafana OSS
- Loki
- Grafana Alloy

## Development Notes

This repository is intended to remain portable between development VMs and persistent Linux hosts.

Application code, dashboards, monitoring templates, and documentation belong in Git. Machine-specific runtime state does not.

A useful separation is:

```text
Repository                       Runtime host
------------------------------   --------------------------------
Python source                    /var/log/smartpanel/
Shell scripts                    /var/lib/loki/
Dashboard exports                /var/lib/alloy/
Monitoring reference configs     /etc/loki/
Documentation                    /etc/alloy/
                                 credentials / secrets
```

## Planned Improvements

- Shared SmartPanel protocol/client module
- Additional SmartPanel state-display modes
- API authentication and authorization
- Grafana provisioning
- Packaged systemd/deployment templates
- Additional health and failure metrics
- Broader SmartPanel model/firmware validation

---

SmartPanel Snapshot is a prototype, but the architecture is intentionally practical: **capture a known-good state, detect drift, make the state observable, and provide a controlled path back to compliance.**

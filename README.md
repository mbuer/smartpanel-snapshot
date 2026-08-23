# SmartPanel Snapshot

> ## Copilot-Assisted Code
>
> This project was developed with the assistance of AI coding tools.
>
> It is not intended for production use. Instead, it is an experimental and educational project that illustrates what is possible today with API access, basic programming knowledge, curiosity, and AI assistance.
>
> The goal is to explore, learn, and demonstrate how these tools can help turn ideas into working prototypes.


SmartPanel Snapshot is a Python-based utility for discovering, monitoring, saving, comparing, and restoring Riedel SmartPanel runtime states through the SmartPanel Live View WebSocket interface.

The project is intended for operational monitoring, configuration drift detection, and recovery of SmartPanel key states.

## Features

- Discover SmartPanels across a configurable IPv4 subnet
- Save SmartPanel runtime states as JSON snapshots
- Compare live state against saved snapshots
- Calculate configuration compliance
- Restore missing or unexpected key states
- Verify SmartPanel identity before restore
- Experimental key normalization
- REST API using FastAPI
- Runtime network configuration through the API
- JSON metrics generation
- Grafana Alloy / Loki logging
- Concurrent multi-panel scanning and health checks

## Architecture

```text
SmartPanels
    │
    │ WebSocket / Live View
    ▼
SmartPanel Snapshot
    ├── save.py
    ├── compare.py
    ├── restore.py
    └── smartpanel_api.py
           │
           ├── snapshots/
           ├── metrics/
           └── JSONL health log
                    │
                    ▼
                  Alloy
                    │
                    ▼
                   Loki
                    │
                    ▼
                  Grafana
```

The current development environment sends logs through Alloy to Grafana Cloud.

A local Grafana OSS + Loki deployment is planned for the production NUC.

## Requirements

- Linux
- Python 3
- Network access to the SmartPanel management network
- SmartPanels with Live View WebSocket access

Python dependencies are listed in:

```text
requirements.txt
```

## Installation

Clone the repository:

```bash
git clone <repository-url>
cd smartpanel-snapshot
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Site-specific configuration is stored in:

```text
config.yaml
```

Example:

```yaml
smartpanels:
  network: "10.85.226.64/26"
  scan_concurrency: 20
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

The discovery network can also be changed through the REST API.

## Save Snapshots

Discover SmartPanels and save their current states:

```bash
./save_all.sh
```

Or scan a specific IP/network directly:

```bash
python save.py 10.85.226.64/26
```

Snapshots are stored in:

```text
snapshots/
```

Example:

```text
snapshots/R3-1232.json
```

> SmartPanel custom names currently need to be unique because the panel name is used as the snapshot filename.

## Check Configuration

Compare all saved snapshots against the current SmartPanel states:

```bash
./check_all.sh
```

Check an individual panel:

```bash
python compare.py snapshots/R3-1232.json
```

The comparison reports:

- Missing Listen states
- Extra Listen states
- Missing Call states
- Extra Call states
- Compliance percentage
- COMPLIANT / NON-COMPLIANT status

Latest results are also written to:

```text
metrics/
```

and:

```text
/var/log/smartpanel/panel-health.jsonl
```

## Restore Configuration

Restore all saved SmartPanel states:

```bash
./restore_all.sh
```

Restore one panel:

```bash
python restore.py snapshots/R3-1232.json
```

The restore process:

1. Connects to the saved panel IP
2. Verifies the SmartPanel name
3. Reads the current state
4. Calculates configuration drift
5. Removes unexpected states
6. Restores missing states
7. Reads the panel again
8. Verifies final compliance

### Experimental Key Normalization

Optional key normalization can be performed before restore:

```bash
./restore_all.sh --normalize
```

or:

```bash
python restore.py snapshots/R3-1232.json --normalize
```

Key normalization is an **experimental feature**.

The implementation does not use a dedicated normalization API. Instead, it simulates touchscreen interactions through the SmartPanel Live View WebSocket interface.

Important limitations:

- Touchscreen coordinates are based on the currently tested SmartPanel layout.
- Panel-type-specific behavior is not fully implemented or validated.
- Simulated touchscreen interactions can occasionally be missed by the panel.
- Normalization should therefore be considered best-effort rather than deterministic.
- Other SmartPanel models, firmware versions, or display layouts may require different touchscreen coordinates.

This feature demonstrates how existing API functionality can be combined creatively to automate an operation even when a dedicated API method is not available.

## REST API

Start the API:

```bash
uvicorn smartpanel_api:app --host 0.0.0.0 --port 8081
```

Interactive API documentation is available at:

```text
http://<server-ip>:8081/docs
```

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

Example network configuration:

```json
{
  "network": "10.85.226.64/26"
}
```

CIDR input is validated and automatically normalized.

## Observability

`compare.py` writes one JSON event per comparison to:

```text
/var/log/smartpanel/panel-health.jsonl
```

Grafana Alloy tails this file and forwards the events to Loki.

Current development path:

```text
SmartPanel Snapshot
        ↓
      JSONL
        ↓
      Alloy
        ↓
Grafana Cloud Loki
        ↓
     Grafana
```

Planned production NUC path:

```text
SmartPanel Snapshot
        ↓
      JSONL
        ↓
      Alloy
        ↓
   Local Loki
        ↓
  Grafana OSS
```

Do not commit Grafana Cloud API tokens or other credentials to this repository.

## Current State Interpretation

The currently tested SmartPanel operating mode determines active states from the Live View LED-ring data:

- Listen: upper LED ring green
- Call/Talk: lower LED ring red

Other SmartPanel operating modes may represent state using different Live View fields, such as key-border colors.

Support for these modes is planned.

## Safety

Restore operations actively change SmartPanel runtime states.

Before using restore functionality on production systems:

- Verify the snapshot belongs to the intended panel
- Verify network connectivity and panel identity
- Test against the relevant SmartPanel firmware/layout
- Restrict API access to trusted networks

The REST API currently does not provide authentication.

## Planned Improvements

- Local Loki + Grafana OSS deployment for the company NUC
- Shared SmartPanel protocol/client module
- Additional SmartPanel state-display modes
- API authentication/authorization
- Grafana provisioning and dashboards
- Improved deployment/service automation

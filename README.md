# Security Camera Threat Detection Demo

Security camera demo showing how **Expanso** processes camera telemetry at the edge before exposing only actionable alerts.

## What this demo highlights

- Multiple camera zones (lobby, loading dock, server room)
- Edge-side filtering (drop noisy motion events)
- Lightweight enrichment (risk score + alert classification)
- Clean downstream output for dashboard/automation

## Minimal structure

```text
.
├── .demo.yaml
├── README.md
├── public/
│   └── index.html
├── pipelines/
│   └── demo/
│       └── security-camera-events.yaml
└── docs/
    └── DEMO_SCRIPT.md
```

## Quick start

```bash
cd /mnt/data/openclaw-repos/demos-security-cameras
# Serve static page locally if needed:
python3 -m http.server 43050 --directory public
# open http://localhost:43050
```

## Pipeline intent

The pipeline spec in `pipelines/demo/security-camera-events.yaml` is a starter skeleton for:

1. ingesting camera event JSON,
2. classifying suspicious activity,
3. emitting only high-signal alerts.

Adapt source/sink blocks to your runtime environment.

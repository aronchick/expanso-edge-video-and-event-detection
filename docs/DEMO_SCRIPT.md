# Demo Script (Security Cameras)

## 1) Setup
- Open the landing page at `public/index.html`
- Explain camera zones: lobby, dock, server room

## 2) Problem
- Raw camera events are noisy
- Sending all raw data downstream is expensive and hard to operationalize

## 3) Expanso value
- Pipeline filters low-signal events at the edge
- Events are enriched with risk score + classification
- Downstream systems consume compact, high-value alerts

## 4) Close
- Replace placeholder file source/sink in `pipelines/demo/security-camera-events.yaml` with your real ingest + alert endpoints
- Integrate with ticketing/notification for critical events

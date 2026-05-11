# Edge-ISR demo orchestration.
#
# Two flows:
#   - Laptop fake-multi (no GPU, no cameras):  up / down / clean / logs / status
#   - Jetson Expanso deploy:                   deploy / undeploy / redeploy / deploy-status
# Plus:                                        bump-sha  (pin all three YAMLs to origin/main)
#
# Run `make help` to list targets.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

STATE_DIR  := .demo-state
ORCH_PID   := $(STATE_DIR)/orchestrator.pid
SENSOR_PID := $(STATE_DIR)/sensor.pid
ORCH_LOG   := $(STATE_DIR)/orchestrator.log
SENSOR_LOG := $(STATE_DIR)/sensor.log
PORT       := 8080

JOB_FILES := jobs/orchestrator-job.yaml jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml
JOB_NAMES := fusion-node sensor-north sensor-south

# ── help ────────────────────────────────────────────────────────────────────
help:  ## list targets
	@awk 'BEGIN{FS=":.*?## "}/^[a-zA-Z][a-zA-Z0-9_-]*:.*## /{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

# ── laptop fake-multi flow ─────────────────────────────────────────────────
up: $(STATE_DIR)  ## start orchestrator + fake-multi sensor; wait for events
	@if [ -f $(ORCH_PID) ] && kill -0 $$(cat $(ORCH_PID)) 2>/dev/null; then \
	  echo "orchestrator already running (PID $$(cat $(ORCH_PID))); run 'make down' first"; exit 1; \
	fi
	@echo "→ starting orchestrator on port $(PORT)…"
	@uv run edge-orchestrator --port $(PORT) > $(ORCH_LOG) 2>&1 & echo $$! > $(ORCH_PID)
	@for i in $$(seq 1 15); do \
	  curl -fsS http://localhost:$(PORT)/metrics > /dev/null 2>&1 && break; sleep 1; \
	done
	@curl -fsS http://localhost:$(PORT)/metrics > /dev/null \
	  || { echo "orchestrator failed to come up; see $(ORCH_LOG)"; exit 1; }
	@echo "→ starting fake-multi sensor…"
	@uv run edge-sensor --fake --multi \
	    --orchestrator http://localhost:$(PORT) --cadence 0.6 \
	    > $(SENSOR_LOG) 2>&1 & echo $$! > $(SENSOR_PID)
	@for i in $$(seq 1 15); do \
	  events=$$(curl -fsS http://localhost:$(PORT)/metrics 2>/dev/null \
	    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total_events",0))' \
	    2>/dev/null || echo 0); \
	  if [ "$$events" -gt 0 ]; then break; fi; sleep 1; \
	done
	@kill -0 $$(cat $(SENSOR_PID)) 2>/dev/null \
	  || { echo "sensor failed; see $(SENSOR_LOG)"; exit 1; }
	@echo
	@echo "  ✓ orchestrator PID $$(cat $(ORCH_PID))  log: $(ORCH_LOG)"
	@echo "  ✓ sensor PID       $$(cat $(SENSOR_PID))  log: $(SENSOR_LOG)"
	@echo
	@echo "  dashboard:  http://localhost:$(PORT)"
	@echo "  metrics:    curl http://localhost:$(PORT)/metrics"
	@echo "  jobs panel: curl http://localhost:$(PORT)/jobs"
	@echo "  follow:     make logs"
	@echo "  stop:       make down"

down:  ## stop orchestrator + sensor; verify port $(PORT) free
	@for f in $(SENSOR_PID) $(ORCH_PID); do \
	  if [ -f $$f ]; then \
	    pid=$$(cat $$f); \
	    if kill -0 $$pid 2>/dev/null; then kill $$pid 2>/dev/null && echo "  killed $$pid"; fi; \
	    rm -f $$f; \
	  fi; \
	done
	@for i in $$(seq 1 5); do \
	  lsof -nP -iTCP:$(PORT) -sTCP:LISTEN > /dev/null 2>&1 || { echo "  ✓ port $(PORT) free"; exit 0; }; \
	  sleep 1; \
	done; \
	echo "  ⚠ port $(PORT) still listening after teardown"; \
	lsof -nP -iTCP:$(PORT) -sTCP:LISTEN; exit 1

clean: down  ## stop + remove transient state (db, ndjson, logs)
	@rm -f orchestrator.db events.ndjson sensor-north.db sensor-south.db
	@rm -rf $(STATE_DIR)
	@echo "  ✓ removed: orchestrator.db, events.ndjson, sensor-*.db, $(STATE_DIR)/"

logs:  ## tail orchestrator + sensor logs interleaved
	@test -f $(ORCH_LOG) -a -f $(SENSOR_LOG) \
	  || { echo "no logs; run 'make up' first"; exit 1; }
	@tail -F $(ORCH_LOG) $(SENSOR_LOG)

status:  ## show process + metrics state
	@for label in orchestrator sensor; do \
	  pidfile=$(STATE_DIR)/$$label.pid; \
	  if [ -f $$pidfile ] && kill -0 $$(cat $$pidfile) 2>/dev/null; then \
	    echo "  $$label: running (PID $$(cat $$pidfile))"; \
	  else \
	    echo "  $$label: not running"; \
	  fi; \
	done
	@echo
	@curl -fsS http://localhost:$(PORT)/metrics 2>/dev/null \
	  | python3 -m json.tool 2>/dev/null \
	  || echo "  (no /metrics reachable)"

$(STATE_DIR):
	@mkdir -p $@

# ── Jetson Expanso deploy ──────────────────────────────────────────────────
deploy:  ## deploy + start all three Expanso jobs
	@command -v expanso-cli > /dev/null \
	  || { echo "expanso-cli not on PATH"; exit 1; }
	@for f in $(JOB_FILES); do \
	  echo "→ deploying $$f"; \
	  expanso-cli job deploy $$f; \
	done
	@for n in $(JOB_NAMES); do \
	  echo "→ starting $$n"; \
	  expanso-cli job start $$n; \
	done
	@echo "  ✓ deployed. status: make deploy-status"

undeploy:  ## stop + remove all three Expanso jobs
	@command -v expanso-cli > /dev/null \
	  || { echo "expanso-cli not on PATH"; exit 1; }
	@for n in $(JOB_NAMES); do \
	  echo "→ stopping $$n"; \
	  expanso-cli job stop $$n 2>/dev/null || echo "  (not running)"; \
	done
	@for n in $(JOB_NAMES); do \
	  echo "→ removing $$n"; \
	  expanso-cli job rm $$n 2>/dev/null || echo "  (not found)"; \
	done

redeploy: undeploy deploy  ## undeploy then deploy (atomic version bump)

deploy-status:  ## show cluster state for the three jobs
	@command -v expanso-cli > /dev/null \
	  || { echo "expanso-cli not on PATH"; exit 1; }
	@expanso-cli job list --format json \
	  | python3 -c 'import json,sys; \
names=set("$(JOB_NAMES)".split()); \
data=json.load(sys.stdin); \
items=data if isinstance(data,list) else data.get("jobs",[]); \
[print(f"  {j[\"spec\"][\"name\"]:18s} {j.get(\"status\",{}).get(\"state\",{}).get(\"state_type\",\"?\")}") \
 for j in items if j.get("spec",{}).get("name") in names]'

# ── SHA bump (atomic across all three YAMLs) ───────────────────────────────
bump-sha:  ## pin all three YAMLs to origin/main HEAD
	@git fetch origin main --quiet
	@sha=$$(git rev-parse origin/main); \
	echo "→ pinning to $$sha"; \
	for f in $(JOB_FILES); do \
	  sed -i.bak -E "s|(expanso-edge-video-and-event-detection)@[0-9a-f]{40}|\1@$$sha|g" $$f \
	    && rm -f $$f.bak; \
	done
	@git --no-pager diff --stat $(JOB_FILES)

.PHONY: help up down clean logs status deploy undeploy redeploy deploy-status bump-sha

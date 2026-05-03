# Stage Runbook — Edge ISR Demo (armyx-tech)

**Print this. Tape it to the laptop.** One page, one job: keep you on script when adrenaline kicks in.

---

## One-time bootstrap (do this on day 1, not on stage)

```bash
# Create S3 bucket, IAM user, scoped keys, deploy archive pipeline.
# Idempotent — safe to re-run.
JETSON_HOST=nvidia@jetson.local \
  ARMYX_EXPANSO_ENDPOINT=https://<your-cluster>.cloud.expanso.io:9010 \
  ARMYX_EXPANSO_API_KEY=exp_ak_... \
  ./scripts/bootstrap_armyx_tech.sh
```

After that you should have:
- `~/.aws/credentials` profile `[armyx-tech]` on Mac and Jetson
- `.env` with `ARMYX_S3_BUCKET`, `ARMYX_JETSON_HOST`, `ARMYX_AWS_PROFILE`
- `expanso-cli profile current` reports `armyx-tech`
- `expanso-cli job describe armyx-tech-event-archive` shows the pipeline

---

## 30-second pre-flight (run before judges enter)

1. Dashboard fullscreen on the conference monitor (F11 in the browser).
2. Look at the screen — verify all of:
   - Header brand has a green dot (not gray).
   - `events/min` and `fused` counters at 0.
   - Cloud pill is **green** "CLOUD LINK · UP".
   - Trigger bar shows chips. **NO `drone`. NO `airplane`.** (If they're there, run the reset curl below — Beat 4's surprise depends on this.)
   - Both sector tiles show camera feeds with green "live" badges.
   - EXPANSO PLATFORM tile shows **4 dots, all green**: fusion-node, sensor-north, sensor-south, armyx-tech-event-archive.
   - **Cloud egress tile** (right of platform): bucket name visible, state badge **green "live"**, object count climbing (or stable from rehearsal).
   - Footer: "4/4 jobs · 0 events · 0 fused".
3. Run `scripts/precheck.sh` — must report 0 fail.
4. Open a second browser tab to the **AWS S3 console** at the bucket so you can switch to it during Beat D if a judge asks for independent verification (the dashboard shows the same data, but having the AWS console primed is belt-and-suspenders).
5. Hands off the keyboard. Wait.

---

## Operator keystrokes — one row per beat

Keyboard focus must be on the dashboard browser tab for these to work. If unsure, click anywhere on the dashboard background once.

| Beat | Keystroke | What it does | Curl backup |
|---|---|---|---|
| **3** (fusion fires) | (none — automatic when both sectors hit within 5s) | If fusion *doesn't* fire and you need to bail mid-Beat-3: **F3** for a synthetic one | `curl -X POST http://localhost:8080/demo/fused-test` |
| **4** (fleet adapts) | **F4** | Adds `drone` to triggers — one chip animates in with green pulse against the existing 2 (`person`, `backpack`); all sensors pick up the new config in <1s | `curl -X POST http://localhost:8080/triggers -H "Content-Type: application/json" -d '{"triggers":["person","backpack","drone"]}'` |
| **5A** (cluster offline) | **F1** | SSH to Jetson, `nmcli radio wifi off` — cluster drops off Expanso Cloud + AWS. Mac LAN dashboard keeps painting; Cloud egress tile flips to **stalled / queued at edge**; Cloud DOWN banner shows | `curl -X POST http://localhost:8080/demo/wan-down`  *(or yank Jetson power-pin Wi-Fi cable for theatre — F1 is cleaner)* |
| **5B** (pipeline edited while offline) | (do it in Expanso Cloud UI, in your second browser tab — edit the `armyx-tech-event-archive` job, save) | Cloud has new pipeline version; cluster can't see it yet. Dashboard tile is unchanged: count still plateau, queue growing | (browser action) |
| **5C** (back online) | **F2** | SSH to Jetson, `nmcli radio wifi on` — cluster reconnects, pulls latest pipeline, drains buffered events to S3 with the new transformation. Cloud egress tile pulses **bumped** as count surges; banner clears | `curl -X POST http://localhost:8080/demo/wan-up` |
| **5D** (independent S3 verification) | (mouse-click any recent key in the Cloud egress tile) | Modal opens showing the JSON contents of that S3 object. Use this to show that pre-offline objects don't have the field added in 5B; post-reconnect ones do | `aws --profile armyx-tech s3 ls s3://${ARMYX_S3_BUCKET}/events/ --recursive` |
| **7** (Q&A only) | (none — leave dashboard up) | Walk through the four jobs / tier strip / topology canvas if a judge asks "how does this work?" Optional live demo: stop a sensor in Cloud UI, watch dashboard go 3/4 → start it back, watch return to 4/4 | (browser action) |

Reset between rehearsals (back to T=0 trigger state):
```
curl -X POST http://localhost:8080/triggers -H "Content-Type: application/json" \
  -d '{"triggers":["person","backpack","handbag","suitcase","knife","scissors","cell phone","laptop","car","truck","bus","bicycle","motorcycle"]}'
```

---

## Recovery moves — if something goes sideways

| If you see... | Do this |
|---|---|
| Dashboard frozen, WS reconnecting banner | Just wait 2s — auto-reload is wired. Don't touch anything. |
| F1-F4 don't respond | Click once on the dashboard background, try again. Falls back to terminal curl. |
| Cloud DOWN banner stuck | F2. If F2 doesn't work, run `ssh ${ARMYX_JETSON_HOST} sudo nmcli radio wifi on` directly. |
| Trigger chip didn't pulse on F4 | Verify the curl with `curl http://localhost:8080/triggers` — if `drone` is in the list, the animation just missed; the demo state is still correct. Move on. |
| Fused alert never fires in Beat 3 | F3 to fire a synthetic one. Narrate over it; judges won't know the difference. |
| Real Reolink camera died | The sector tile will go gray ("offline" badge). Don't acknowledge it; move to the live sector. The fusion node + other sensor keep working. |
| Cloud egress tile says "not configured" | `.env` lost `ARMYX_S3_BUCKET`. Restart `edge-orchestrator` after `set -a; source .env; set +a`. |
| Cloud egress tile "stalled" but you didn't press F1 | The Jetson's Wi-Fi association may have rolled over. Check `ssh ${ARMYX_JETSON_HOST} nmcli device status`. The pipeline is buffering; don't panic — F2 will drain on the next reconnect. |
| S3 object viewer modal shows "error: NoSuchKey" | The eventually-consistent list was ahead of the read. Wait 2s, click again. |
| Whole laptop falls over | Backup recorded video on the secondary device. Spec §16. Do **not** apologize. |

---

## What you must NOT do

- Don't touch the trigger bar before Beat 4 — judges need to see the "before" state for the F4 surprise.
- Don't manually trigger F3 (synthetic fused) before Beat 3 — it'll deplete the surprise of the auto-fusion moment.
- Don't yank the literal WAN cable — F1 is cleaner and reversible. Reserve the cable yank for the on-stage backup if F1 fails.
- Don't apologize for anything that misfires. Acknowledge in one sentence, redirect, keep moving.
- **Don't open the dashboard during Beats 1-2.** Pure narration there. Dashboard reveal happens at Beat 3 — it's the payoff for the wishlist setup.
- **Don't tour the four-job platform tile during the demo proper.** That's Beat 7 / Q&A material. Talking about implementation while delivering Beats 3-5 buries the wishlist landings.
- Don't run long. Drop priority: **Beat 4** (fold "fleet adapts" into Beat 6), then **Beat 5D** (narrate the S3 archive without clicking), then trim **Beat 5A** narration. **Never drop Beat 6.**

---

## After the demo (between rehearsals)

```bash
# One-shot reset: stop the four cluster jobs (specs stay in cluster,
# just paused) + clear local state. This puts you back at "Beat 0
# lights-up" — 0/4 jobs running, gray dots on the dashboard, ready
# for the operator to start them from the Expanso Cloud UI.
./scripts/demo_reset.sh

# Make sure the Jetson is back online (idempotent)
ssh ${ARMYX_JETSON_HOST} sudo nmcli radio wifi on

# If you want a clean S3 archive for the next rehearsal:
aws --profile armyx-tech s3 rm s3://${ARMYX_S3_BUCKET}/events/ --recursive
```

For the NEXT rehearsal, Beat 0 starts the three already-deployed
**workload** jobs from the Expanso Cloud UI tab (operator clicks
Start/Rerun on each in order: sensor-north → sensor-south → archive).
The fusion-node stays running between rehearsals so the dashboard
keeps painting. CLI fallback if the UI is slow:
```bash
expanso-cli job rerun sensor-north
expanso-cli job rerun sensor-south
expanso-cli job rerun armyx-tech-event-archive
```

Initial venue deploy (run **once** per venue, before the first
rehearsal):
```bash
./scripts/demo_deploy_all.sh    # deploys 4 jobs, verifies Running, then stops them
                                # so post-deploy state == Beat 0 lights-up
```

If you used F1 (WAN-down) and forgot to F2 — every subsequent rehearsal will look broken. Always end a run with cloud UP.

To completely tear down and re-bootstrap:
```bash
./scripts/teardown_armyx_tech.sh --all   # destructive: removes IAM user + bucket
./scripts/bootstrap_armyx_tech.sh        # rebuild from scratch
```

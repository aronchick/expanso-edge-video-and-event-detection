# Demo Script — Edge ISR (4 minutes)

**This file is the canonical verbal script for the live demo.** All
development work should be driven by what this script promises:
keystrokes, dashboard reactions, talking-point landings, and timings.
If a behavior isn't asked for here, it's not in scope for the demo.
If it is asked for here and the implementation doesn't deliver it,
that's a bug.

Companion docs (read these alongside, but THIS file is the one we
review when deciding what to build):
- `STAGE_RUNBOOK.md` — printable operator cheat sheet (keystrokes + curl backups + recovery moves)
- `DEMO_UI_SPEC.md` — design system the dashboard must match
- `HACKATHON_SCRIPT.md` — full implementation spec (architecture, BOM, network, code, deploy)

Time-boxed beats with verbatim talking points and operator notes.
Practice these. The architectural punchline at the end is the line
you cannot drop.

**Operator setup**: dashboard fullscreen on the conference monitor
(F11). All four operator controls are bound to function keys on the
laptop's keyboard — see STAGE_RUNBOOK.md for the cheat sheet. Curl
backups for every keystroke are also in the runbook in case keyboard
focus drifts.

---

## T = 0 — post-deploy hero shot (end of Beat 0)

What's on the screen once Beat 0's deploy completes — this is the
state Beat 1 starts from:

- **Header**: green "● Edge ISR · Expanso" brand, live `events/min`
  and `fused` counters at 0, big green "CLOUD LINK · UP" pill.
- **Trigger bar**: chips showing `person`, `backpack`, `car`,
  `truck`, etc. — but **no `drone` and no `airplane`** (deliberate,
  sets up Beat 3).
- **Two sector tiles**: live camera feeds, each labeled SECTOR NORTH
  and SECTOR SOUTH, both with green "live" status badges.
- **Recent events panels**: empty.
- **EXPANSO PLATFORM tile**: four green dots — `orchestrator`,
  `sensor-north`, `sensor-south`, `armyx-tech-event-archive`. All
  running.
- **Cloud Egress tile** (right of platform): bucket name visible,
  state badge green `live`, object count climbing (or stable from
  rehearsal).
- **Footer**: "Powered by Expanso · workload moves to the data ·
  4/4 jobs · 0 events · 0 fused".
- **Topology**: bottom-right corner shows N → O and S → O nodes,
  idle.

The hero shot. Don't move into Beat 1 until judges have seen all
four green dots and both live sector feeds.

---

## Beat 0 — start the pipelines from the cloud UI (30s)

**This beat exists so the "pipeline IS the control plane" claim in
Beat 1 lands as something judges literally watched happen, not as
something they have to take on faith.** The four jobs are *already
deployed* in the Expanso Cloud cluster — but stopped. Operator brings
them online from the cloud control-plane UI. Skippable under time
pressure (see guardrail below).

**Operator setup** — what's on the screen at lights-up, BEFORE Beat 0:

- **Conference monitor**: dashboard fullscreen.
- **Operator's laptop screen** (judges see this on the conference
  monitor too, briefly): a second browser tab open to **Expanso
  Cloud UI**, the `armyx-tech` cluster, jobs view — showing all four
  jobs in **Stopped** state. Operator has the Start (or Rerun)
  control visible without scrolling.
- **Dashboard state on the conference monitor**:
  - **Header**: brand dot gray. `events/min` and `fused` at 0. Cloud
    pill **green** "CLOUD LINK · UP" (the Mac's own internet is up
    — only the cluster workload is paused).
  - **Tier strip**: EDGE and ORCHESTRATOR dots gray (no agent
    actively reporting), CLOUD dot green (the cloud control plane
    itself is reachable — that's how we're about to start the jobs).
  - **Trigger bar**: empty / placeholder ("orchestrator stopped").
  - **Two sector tiles**: gray placeholders, badge reads
    "stopped". Snapshots return the synthesized "awaiting start"
    frame.
  - **Recent events panels**: empty.
  - **EXPANSO PLATFORM tile**: **0/4 running**, four gray dots
    labeled `orchestrator`, `sensor-north`, `sensor-south`,
    `armyx-tech-event-archive` — each pill text reads
    "name · stopped".
  - **Cloud Egress tile**: bucket name visible, badge **gray "idle"**,
    object count 0.
  - **Footer**: "Powered by Expanso · workload moves to the data ·
    0/4 jobs · 0 events · 0 fused".
  - **Topology**: gray, no arrows lit.

*[Stand at the laptop. Gesture at the dashboard, then at the cloud
UI on your screen.]*

> "Before I start: every Expanso job for this demo — sensors,
> orchestrator, archive — is already provisioned in the cluster
> you can see in this browser tab. Right now they're all stopped.
> Look at the dashboard: zero of four jobs running."

*[Pause one beat. Let judges see the gray dots and "0/4".]*

> "Everything is one click away. From this cloud control plane,
> right now, I'm going to start all four — orchestrator first,
> then both sensors, then the archive — and watch what fires up
> on the dashboard."

**Operator**: switch focus to the Expanso Cloud UI tab. Click **Start**
(or **Rerun**) on each of the four jobs in order: `orchestrator` →
`sensor-north` → `sensor-south` → `armyx-tech-event-archive`. *(If the
UI offers a "start all" bulk action, use it — the per-job clicks are
the unbulked fallback.)*

*[Switch focus back to the dashboard. The EXPANSO PLATFORM tile dots
flip green one-by-one as each job's Expanso execution reports
Running. The "0/4" counter ticks up: 1/4 … 2/4 … 3/4 … 4/4. Tier
strip's EDGE and ORCHESTRATOR dots go green. Sector tiles light up
with live camera feed as the sensors attach. Trigger bar populates
from the orchestrator's default config.]*

> "Four jobs. Same control plane. Same spec format. Orchestrator,
> two sensors, archive. Started from the cloud, executing on the
> edge. Now we're live."

*[Hold on the fully-green dashboard for one beat. Transition into
Beat 1.]*

**Drop-rule**: if any job hasn't flipped Running by ~10s after the
click, the operator says **"…and we'll come back to that"** and
advances straight to Beat 1, narrating *as if* the start completed
(the dashboard catches up in the background, usually before Beat 2
ends). CLI fallback if the UI is slow or wedged: drop to a terminal
and run `expanso-cli job rerun <name>` for whichever job is still
gray. Reset between rehearsals with `./scripts/demo_reset.sh`
(stops the four jobs without deleting them, so the next rehearsal
starts from the same Beat 0 lights-up state).

---

## Beat 1 — pain point (40s)

*[Stand still next to the screen. Both sectors are quiet. Let them look.]*

> "Today's edge sensor architecture for ISR looks like this. A
> camera is running constantly and its only motion is to ship that video to the cloud. It may be a blank space. It might be the same person standing around, or it could be a threat that needs immediate action. Now, thanks to the advent of AI, we have more and better detection once that video does hit the cloud. Shipping data naively costs your time, costs your response time, makes you more obvious to adversaries, and ultimately can choke your bandwidth, even in the best possible scenarios.
> 
> You can't be treating decision making on the cloud as the only place to go and take these actions. You have to think about multi level intelligence, taking advantage of every resource you have, at every level. This both makes you more responsive, as well as removes single points of failure adversaries will
> absolutely exploit.
> 
>What I'm going to show you is the inversion. Every sensor is its
> own "junior" analyst. The cloud is reachback for richer context, not a
> precondition for the system working at all. Every component you
> see — the sensors, the correlator, the audit pipeline — is the
> same kind of Expanso job, on the same control plane.  The best part about it is that this is a pure augmentation overlay. The same cloud-like intelligence you have in the cloud still works. You're just layering these solutions over the cloud and reusing them as if they had been plugged in without installing any new physical devices. 
> 
> Let's start by looking at the bottom of the screen: those four jobs we just deployed are now running. "

---

## Beat 2 — happy path, single sector (45s)

*[Walk through sector north — empty-handed.]*

> "Person, sector north. Local YOLO on the Jetson. Sub-50
> millisecond inference. The event landed on the dashboard as it
> happened, signed with a DBOM signature you can see in the corner
> of the event card. Nothing reached out to the cloud."

*[Walk through north again, this time wearing or carrying a backpack.]*

> "Person, sector north, carrying a pack. The local sensor decided
> this one was worth richer context — so it called Gemini Flash. You
> see the description appear in the event panel, italicized, with
> the model version that produced it. That's a choice the edge
> made, not the cloud. Watch the topology indicator in the corner —
> that arrow lit up the moment the event left the Jetson and arrived
> at the orchestrator."

---

## Beat 3 — second sector + live class update (50s)

**Operator**: have F4 ready. Backup curl printed in the runbook.

*[Fly the drone through sector south.]*

> "Aerial contact in sector south. Small quadcopter. But watch the
> dashboard — no event surfaced, no description, nothing. The model
> can see it. We just haven't told this system to care about aerial
> contacts yet. Look at the trigger bar at the top — `person`,
> `backpack`, vehicles. No `drone`. No `airplane`."

*[Hold one beat. Walk back to the laptop.]*

> "Changing mission parameters today often means redeploying brand
> new pipelines, sometimes brand new firmware, sometimes brand new
> hardware. Hours to days. What if you could change what the fleet
> is *looking for* right now, while every sensor stays running?
>
> The orchestrator holds the active trigger list. Every sensor
> polls it once a second. I push one config update — and every
> sensor on the network picks it up before I finish the sentence."

**Operator**: press **F4**.

*[The trigger bar animates: a new chip scales in with a green pulse.
The chip says `drone`.]*

> "There. The trigger panel just gained a class — every sensor on
> the network picked that up in under a second. No restart, no
> redeploy, no service interruption."

*[Fly the drone through south again.]*

> "Drone, sector south. Surfaces immediately, signed, with the model
> version that produced the detection. This is what 'updating an edge
> fleet in the field' actually looks like when the workload lives
> next to the data. No truck rolls. No firmware push. One config
> flip and every sensor on the perimeter is now watching for a new
> thing."

---

## Beat 4 — cross-sensor fusion (40s)

*[With a partner if possible: walk through north while a drone flies
through south, simultaneously. If solo: trigger drone first, then
immediately walk through north — within a 5-second window.]*

> "Person, sector north. Drone, sector south. Simultaneous. Watch."

*[Pause one beat. The full-screen FUSED ALERT overlay takes over the
dashboard with both contacts side-by-side in 60-pixel type, amber on
black.]*

> "Multi-sector correlation. Two sectors, two contact types, fused
> locally on the orchestrator. No human did that correlation. The
> system did, in under five seconds, without anything reaching back
> to a cloud. This is the fusion problem that almost nothing in
> production solves today."

*[The overlay collapses after 3.5 seconds back to the live dashboard.]*

---

## Beat 5 — DDIL + cloud control plane (90s, four sub-beats A/B/C/D)

This beat replaces the original "WAN-yank, sensors degrade
gracefully" demo with a real, end-to-end control-plane story:

1. **A** — take the cluster offline, prove edge keeps working.
2. **B** — update the pipeline in Expanso Cloud *while* offline;
   cluster keeps doing the OLD thing because it can't see the change.
3. **C** — bring the cluster back online; new pipeline applies, queue
   drains.
4. **D** — show the S3 archive growing along the way, with
   independently verifiable data.

The 4-beat flow is what the demo is built around: the Mac retains
its own internet path the entire time, so the Cloud Egress tile and
the AWS S3 console both keep showing truth even while the Jetson is
off the world.

**Operator setup**: Cloud Egress tile (right of Expanso Platform
tile) should show **`live`** with a non-zero object count — events
are flowing through the `armyx-tech-event-archive` pipeline to S3. A
second browser tab is open to the Expanso Cloud UI with the
`armyx-tech-event-archive` job opened, ready to edit.

### Beat 5A — take the cluster offline (15s)

> "Two cameras, sensors firing locally, AND every event is also being
> shipped to S3 in real time through a dedicated Expanso pipeline —
> the cloud tier is doing exactly the job it's good at. Watch the
> bottom-right tile: that count climbs every time something hits a
> sector."

*[Pause one beat so judges see the count tick.]*

> "Now — the part of this architecture that adversaries are going
> to test for you: what happens when the link to the cloud tier
> goes away?"

**Operator**: press **F1**.  *(Mac SSHes the Jetson and runs
`sudo nmcli radio wifi off`.)*

*[Full-width red banner slams in from the top: CLOUD LINK · DOWN.
Dashboard gets a pulsing red border. The Cloud Egress tile flips its
state badge to amber: **stalled · queued at edge**. Object count
plateaus.]*

> "Jetson lost its Wi-Fi. It can't see Expanso Cloud, it can't see
> AWS. The cloud tier — which was doing real work a second ago — is
> simply *not reachable* from this Jetson right now. But — look at
> the dashboard. Both cameras still live. Detections still happening.
> The dashboard you're looking at is on the wired LAN between this
> Mac and the Jetson, not on Wi-Fi. *That's intentional* — the
> operator's tablet talks to the sensor over a private link, not
> over the same uplink that's been cut."

*[Walk through both sectors. Events surface as usual on the
dashboard. The Cloud Egress count stays frozen.]*

> "Events still detected. Still signed. Still landing in the local
> archive on the Jetson. The Expanso pipeline that ships them to
> S3 is *buffering at the edge* — that's what 'queued' means in the
> egress tile. The cloud's job is on pause. The edge's job — which
> never depended on the cloud being up — keeps going. Nothing is
> lost. The pipeline will deliver, when it can."

### Beat 5B — update the pipeline while the cluster is offline (25s)

> "While the Jetson is dark, my colleague at HQ is going to do
> something interesting."

**Operator**: switch to the Expanso Cloud browser tab. Open the
`armyx-tech-event-archive` pipeline. Add a small visible field —
e.g. add `root.archive.classification = "DEMO/UNCLASSIFIED"` to the
lineage processor. Save.

> "I just edited the pipeline in Expanso Cloud. Added a
> classification marker to every archived event. The cloud has the
> new version. The Jetson does not — it's offline, it can't see
> this update."

*[Walk through one of the sectors. New event surfaces on the
dashboard. The Cloud Egress tile is still stalled, count still
plateau.]*

> "And notice — the Jetson is still capturing events with the *old*
> pipeline definition. That's correct. You don't want a sensor that
> silently changes behavior the second the link blinks. The new
> pipeline is staged in the cloud, waiting for the cluster to come
> back."

### Beat 5C — back online, drain (20s)

**Operator**: press **F2**.  *(Mac SSHes the Jetson and runs
`sudo nmcli radio wifi on`.)*

*[Red banner slides up and out. Cloud Egress tile state flips back
to **`live`**. Object count surges as the buffered events drain —
the count tile pulses **bumped** with each batch.]*

> "Wi-Fi back. The cluster reconnects to Expanso Cloud, *pulls the
> new pipeline definition*, and applies it to everything in the
> buffer. The drain is happening right now — every queued event is
> leaving the edge, going through the new transformation, landing in
> S3."

*[Pause for the count to stabilize at its new total.]*

> "Watch the egress count: it just jumped by every event captured
> during the offline window. Zero data loss. The pipeline that
> processed those events on the way out is the *new* pipeline, the
> one with the classification marker my colleague added. The cloud
> is the control plane. The edge is the workplane. They reconcile
> when the link comes back."

### Beat 5D — independent S3 verification (15s)

**Operator**: click the most recent key in the Cloud Egress
recent-keys list.

*[Modal opens showing the JSON contents of that S3 object — including
the new `archive.classification = "DEMO/UNCLASSIFIED"` field that
wasn't there pre-Beat-5B.]*

> "And just to prove this isn't theatre — every key you're seeing is
> a real S3 object in our AWS bucket, signed at the edge, archived
> through the Expanso pipeline. Click any one and you see the JSON.
> The newer ones have the field my colleague added. The older ones
> don't."

*[Optionally click an older key to show the schema difference.]*

> "Same control plane managed both. Different pipeline versions
> applied at different points in time, with the offline window
> cleanly bracketed."

---

## Beat 6 — the architectural punchline (30s)

*[Step toward the judges. Calm, slow.]*

> "What you just saw is two cameras and a Jetson on a table. Four
> Expanso jobs on the same control plane. A live model-update beat.
> An autonomous fusion event. A deliberate offline window where the
> edge kept working, the cloud kept evolving, and they reconciled
> cleanly when the link came back. And every event from that demo
> is sitting in S3 right now, signed, with provenance.
>
> Every component on the screen — the sensors, the orchestrator,
> the archive pipeline — is the same kind of job spec, managed by
> the same control plane. That spec, with no code changes, deploys
> to the perimeter of a FOB. To a Reaper. To a JTAC's pack. To a
> destroyer's CIC. Or all four at once. The sensor changes. The
> pipeline doesn't.
>
> That's Expanso. Move the workload TO the data — without giving
> up the cloud you already have. Questions."

---

## Time budget (target 4:00, hard ceiling 4:30)

Beat 0 adds 30s to the script. Two postures depending on rehearsal
state:

### Option I — Beat 0 droppable (use for first 3 rehearsals)

| Beat | Target | Cumulative | Drop priority |
|------|--------|------------|---------------|
| Beat 0 — start the pipelines from the cloud UI | 0:30 | 0:30 | **1st** (drop if any job hasn't flipped Running by 10s after click) |
| Beat 1 — pain point | 0:40 | 1:10 | keep |
| Beat 2 — single sector | 0:45 | 1:55 | keep |
| Beat 3 — live trigger update | 0:50 | 2:45 | 2nd |
| Beat 4 — fusion | 0:40 | 3:25 | keep |
| Beat 5 (A+B+C+D) — DDIL + cloud control plane | 1:15 | 4:40 | keep |
| Beat 6 — punchline | 0:30 | 5:10 | **never drop** |

5:10 cumulative is OVER the 4:30 ceiling — acceptable only if
rehearsal proves Beat 0 reliably hits ≤0:25 AND Beat 1 trims to
≤0:35 in delivery. If not: drop Beat 0 first, then Beat 3.

### Option II — Beat 0 included by trimming (after 5 clean rehearsals)

| Beat | Target | Cumulative |
|------|--------|------------|
| Beat 0 — start the pipelines from the cloud UI | 0:30 | 0:30 |
| Beat 1 — pain point + augmentation pitch (trimmed) | 0:25 | 0:55 |
| Beat 2 — single sector | 0:45 | 1:40 |
| Beat 3 — live trigger update | 0:45 | 2:25 |
| Beat 4 — fusion | 0:40 | 3:05 |
| Beat 5 (A+B+C+D) — DDIL + cloud control plane (trimmed 5D) | 1:10 | 4:15 |
| Beat 6 — punchline | 0:25 | 4:40 |

Lands at the absolute hard ceiling. Requires sharp execution and a
demonstrated ≤25s "click-to-all-green" in the Expanso Cloud UI over
5 cold runs (rehearse with `./scripts/demo_reset.sh` between runs to
return to the deployed-but-stopped Beat 0 state).

### Drop-rule (live, on stage)

- If you hit **1:55** cumulative and haven't started Beat 2: drop
  Beat 0's narrative recap line ("four jobs we just deployed") and
  accelerate.
- If you hit **3:30** and haven't done DDIL: drop Beat 3 immediately
  and go Beat 4 → 5 → 6.
- The architectural punchline (Beat 6) absolutely cannot be cut.

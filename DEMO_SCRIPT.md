# Demo Script — Edge ISR (4 minutes)

**This file is the canonical verbal script for the live demo.** All
development work should be driven by what this script promises:
keystrokes, dashboard reactions, talking-point landings, and timings.
If a behavior isn't asked for here, it's not in scope for the demo.
If it is asked for here and the implementation doesn't deliver it,
that's a bug.

**Narrative order matters.** This script opens with the **pain** and
the **operator's wishlist**, NOT with a tour of the dashboard. Every
demo moment after that maps to a wishlist item the audience asked
for. Expanso is the *enabler that makes the solution possible* — not
the subject of the demo. The deep tech tour is Beat 7 (optional, kept
on screen during Q&A) — judges have to see the *outcome* before they
can care about the implementation.

Companion docs (read these alongside, but THIS file is the one we
review when deciding what to build):
- `STAGE_RUNBOOK.md` — printable operator cheat sheet (keystrokes + curl backups + recovery moves)
- `DEMO_UI_SPEC.md` — design system the dashboard must match
- `HACKATHON_SCRIPT.md` — full implementation spec (architecture, BOM, network, code, deploy)

Time-boxed beats with verbatim talking points and operator notes.
Practice these. The architectural punchline at the end is the line
you cannot drop.

**Operator setup**: dashboard fullscreen on the conference monitor
(F11) but **on a black/blank slide or hidden behind another window
during Beats 1-2** — the dashboard is a payoff, not a backdrop.
Operator controls bound to function keys; see `STAGE_RUNBOOK.md`.
Curl backups for every keystroke are also in the runbook in case
keyboard focus drifts.

---

## Beat 1 — the pain (45s)

**Dashboard hidden / black slide.** Pure narration. The audience
should be feeling the problem, not looking at a UI yet.

*[Stand square to the audience. No screen distraction.]*

> "Today's edge sensor architecture for ISR looks like this. A
> camera is running constantly. Its only job is to ship video back
> to the cloud, where someone — or something — eventually decides
> whether what it saw matters. That video might be a blank parking
> lot. It might be the same person standing in the same place for
> three hours. It might be the threat that needs immediate action.
> The camera doesn't know. So it ships everything.
>
> Shipping everything costs you four things. It costs **bandwidth**
> you don't have on a contested link. It costs **time** — the
> latency of the round-trip is the latency of your decision. It
> makes you **visible** — every byte going up is an emission an
> adversary can detect. And it gives you a **single point of
> failure** the adversary will absolutely exploit.
>
> The cloud is good at things. Big models. Cross-mission analytics.
> Long-horizon retention. We don't want to give those up. But we
> can't be **dependent** on them to know whether the thing we're
> looking at is a threat. The decision has to live where the data
> lives — at the edge — and the cloud has to be a *bonus*, not a
> precondition."

*[Hold one beat. Transition.]*

> "So — what would the operator on the ground actually want?"

---

## Beat 2 — what winning looks like (45s)

**Dashboard still hidden.** State the wishlist as five outcomes the
operator on the ground would name if you asked them. Each one becomes
a delivered moment in the next three beats.

*[Count off on fingers if it helps. The list is the spine of the
rest of the demo.]*

> "Five things. If I were that operator on the ground, here's what
> I'd want from my sensor:
>
> **One.** Detection happens local. Always. Even when the link to
> the cloud is gone, my sensor still tells me what it sees.
>
> **Two.** When the cloud *is* available, I get richer context for
> free — the same big-model intelligence, but only on the events
> that need it.
>
> **Three.** When two of my sensors see something at the same time,
> they correlate locally. I don't wait for a TOC to fuse it for me.
>
> **Four.** When my mission changes — a new threat class, a new
> classified target — I push the change once and every sensor on
> the perimeter picks it up before I finish the sentence. No truck
> rolls. No firmware push.
>
> **Five.** When the link drops — and it *will* drop — nothing is
> lost. Events queue. Provenance is preserved. When the link comes
> back, everything reconciles. End-to-end. Sub-second."

*[Pause one beat. Transition into the demo proper.]*

> "Let me show you all five of those, working, on this table, right
> now."

**Operator**: bring the dashboard to the foreground (F11 / Cmd-Tab
to the browser tab). Transition into Beat 3.

---

## T = 0 — what the dashboard shows when Beat 3 begins

This is the hero shot the judges see for the first time at the
start of Beat 3. The dashboard was hidden during Beats 1-2; this is
its reveal.

- **Header**: green "● Edge ISR · Expanso" brand, live `events/min`
  and `fused` counters at 0, big green "CLOUD LINK · UP" pill.
- **Tier strip**: EDGE / FUSION / CLOUD all green.
- **Trigger bar**: chips showing `person`, `backpack`, `car`,
  `truck`, etc. — but **no `drone` and no `airplane`** (deliberate,
  sets up Beat 4).
- **Two sector tiles**: live camera feeds, each labeled SECTOR NORTH
  and SECTOR SOUTH, both with green "live" status badges.
- **Recent events panels**: empty.
- **EXPANSO PLATFORM tile**: four green dots — `fusion-node`,
  `sensor-north`, `sensor-south`, `armyx-tech-event-archive`. All
  running.
- **Cloud Egress tile** (right of platform): bucket name visible,
  state badge green `live`, object count climbing.
- **Footer**: "Powered by Expanso · workload moves to the data ·
  4/4 jobs · 0 events · 0 fused".
- **Topology**: bottom-right corner shows N → F and S → F nodes,
  idle.

The hero shot. Don't move into Beat 3 dialogue until judges have had
1-2 seconds to take it in. *Then* start delivering wishlist items.

---

## Beat 3 — the solution in action (75s) — delivers wishlist items 1, 2, 3

This is the first time judges see the dashboard. Each visual moment
explicitly maps back to one of the five wishlist items they just
heard.

*[Walk through sector north — empty-handed.]*

> "Person, sector north. Local YOLO on the Jetson. Sub-50
> millisecond inference. The event landed on the dashboard the
> moment it happened — signed with a DBOM signature you can see
> in the corner of the event card. Nothing reached out to the
> cloud. **Wishlist item one — local detection — delivered.**"

*[Walk through north again, this time wearing or carrying a backpack.]*

> "Same sensor. Now I'm carrying something. The local sensor decided
> *this one* deserves richer context — so it called Gemini Flash.
> You see the description appear in the event panel, italicized,
> with the model version that produced it. That's a choice the
> *edge* made, not the cloud. The cloud is augmentation. **Wishlist
> item two — cloud as bonus — delivered.**"

*[With a partner if possible: walk through north while a drone flies
through south, simultaneously. If solo: trigger drone first, then
immediately walk through north — within a 5-second window.]*

> "Person, sector north. Drone, sector south. Simultaneous. Watch."

*[Pause one beat. The full-screen FUSED ALERT overlay takes over the
dashboard with both contacts side-by-side in 60-pixel type, amber on
black.]*

> "Multi-sector correlation. Two sectors, two contact types, fused
> locally on the fusion node. No human did that correlation. The
> system did, in under five seconds, without anything reaching back
> to a cloud. **Wishlist item three — local correlation — delivered.**"

*[Overlay collapses after 3.5 seconds back to the live dashboard.]*

---

## Beat 4 — the fleet adapts in seconds (40s) — delivers wishlist item 4

**Operator**: have F4 ready. Backup curl printed in the runbook.

*[Fly the drone through sector south.]*

> "Aerial contact in sector south. Small quadcopter. But watch the
> dashboard — no event surfaced, no description, nothing. The model
> can see it. We just haven't told this system to care about aerial
> contacts yet. Look at the trigger bar at the top — `person`,
> `backpack`, vehicles. No `drone`. No `airplane`."

*[Hold one beat. Walk back to the laptop.]*

> "Changing what the fleet is *looking for*, in the field, today —
> that's hours to days. New pipelines. Sometimes new firmware.
> Sometimes new hardware. What if you could change it right now,
> while every sensor stays running?"

**Operator**: press **F4**.

*[The trigger bar animates: a new chip scales in with a green pulse.
The chip says `drone`.]*

> "There. The fusion node holds the active trigger list. Every
> sensor polls it once a second. That config update I just pushed —
> every sensor on the network has it now. No restart. No redeploy.
> No service interruption."

*[Fly the drone through south again.]*

> "Drone, sector south. Surfaces immediately, signed, with the
> model version that produced the detection. **Wishlist item four —
> the fleet adapts in seconds — delivered.**"

---

## Beat 5 — surviving reality (90s, four sub-beats A/B/C/D) — delivers wishlist item 5

This is the demo's hardest beat to land — and the one that proves
wishlist item 5: zero loss, provenance preserved, end-to-end
reconciliation when the link comes back.

The 4-sub-beat flow: **A** offline, **B** edit pipeline while
offline, **C** reconnect + drain, **D** show the S3 archive
independently. The Mac retains its own internet path the entire
time, so the Cloud Egress tile keeps showing truth even while the
Jetson is off the world.

**Operator setup**: Cloud Egress tile (right of Expanso Platform
tile) should show **`live`** with a non-zero object count — events
are flowing through the `armyx-tech-event-archive` pipeline to S3. A
second browser tab is open to the Expanso Cloud UI with the
`armyx-tech-event-archive` job opened, ready to edit.

### Beat 5A — take the cluster offline (15s)

> "Two cameras, sensors firing locally, AND every event is also
> being shipped to S3 in real time through a dedicated pipeline —
> the cloud tier doing exactly what it's good at. Watch the
> bottom-right tile: that count climbs every time something hits
> a sector."

*[Pause one beat so judges see the count tick.]*

> "Now — the part of this architecture that adversaries are going
> to test for you: what happens when the link to the cloud tier
> goes away?"

**Operator**: press **F1**. *(Mac SSHes the Jetson and runs
`sudo nmcli radio wifi off`.)*

*[Full-width red banner slams in from the top: CLOUD LINK · DOWN.
Dashboard gets a pulsing red border. The Cloud Egress tile flips its
state badge to amber: **stalled · queued at edge**. Object count
plateaus.]*

> "Jetson lost its Wi-Fi. It can't see Expanso Cloud, it can't see
> AWS. The cloud tier — which was doing real work a second ago —
> is simply *not reachable* from this Jetson right now. But — look
> at the dashboard. Both cameras still live. Detections still
> happening. The dashboard you're looking at is on the wired LAN
> between this Mac and the Jetson, not on Wi-Fi. *That's
> intentional* — the operator's tablet talks to the sensor over a
> private link, not over the same uplink that's been cut."

*[Walk through both sectors. Events surface as usual on the
dashboard. The Cloud Egress count stays frozen.]*

> "Events still detected. Still signed. Still landing in the local
> archive on the Jetson. The pipeline that ships them to S3 is
> *buffering at the edge* — that's what 'queued' means in the
> egress tile. The cloud's job is on pause. The edge's job — which
> never depended on the cloud being up — keeps going."

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
> pipeline definition. That's correct. You don't want a sensor
> that silently changes behavior the second the link blinks. The
> new pipeline is staged in the cloud, waiting for the cluster to
> come back."

### Beat 5C — back online, drain (20s)

**Operator**: press **F2**. *(Mac SSHes the Jetson and runs
`sudo nmcli radio wifi on`.)*

*[Red banner slides up and out. Cloud Egress tile state flips back
to **`live`**. Object count surges as the buffered events drain —
the count tile pulses **bumped** with each batch.]*

> "Wi-Fi back. The cluster reconnects, *pulls the new pipeline
> definition*, and applies it to everything in the buffer. The drain
> is happening right now — every queued event leaving the edge,
> going through the new transformation, landing in S3."

*[Pause for the count to stabilize at its new total.]*

> "Watch the egress count: it just jumped by every event captured
> during the offline window. **Wishlist item five — zero loss,
> provenance preserved — delivered.**"

### Beat 5D — independent S3 verification (15s)

**Operator**: click the most recent key in the Cloud Egress
recent-keys list.

*[Modal opens showing the JSON contents of that S3 object — including
the new `archive.classification = "DEMO/UNCLASSIFIED"` field that
wasn't there pre-Beat-5B.]*

> "And just to prove this isn't theatre — every key you're seeing
> is a real S3 object in our AWS bucket, signed at the edge,
> archived through the cloud pipeline. Click any one and you see
> the JSON. The newer ones have the field my colleague added. The
> older ones don't. Same control plane managed both — different
> pipeline versions applied at different points in time, with the
> offline window cleanly bracketed."

---

## Beat 6 — the punchline (30s)

*[Step toward the judges. Calm, slow.]*

> "Five wishlist items, all delivered, on two cameras and a Jetson
> on this table. Local detection. Cloud as bonus. Multi-sensor
> correlation. Sub-second fleet config push. Zero-loss survival
> through a deliberate offline window — with HQ pushing pipeline
> updates the whole time and the edge picking them up on reconnect.
>
> What I just showed you is not a custom build. The same solution,
> same job specs, no code changes — deploys to a FOB perimeter, to
> a Reaper, to a JTAC's pack, to a destroyer's CIC. The sensor
> changes. The solution doesn't.
>
> That's possible because of **Expanso**. Expanso is the substrate
> that makes the solution work — control plane in the cloud,
> workplane on the edge, reconciliation when the link comes back,
> same job spec from sensor to archive. Without that substrate,
> you build all of this yourself. With it, you focus on the
> mission, not the plumbing.
>
> Move the workload TO the data — without giving up the cloud you
> already have. Questions."

---

## Beat 7 — optional deep tech tour (Q&A material — leave on screen)

**Not part of the 4-minute timed demo.** Stays on the dashboard
between Beat 6 and Q&A; pull this material out only if a judge asks
"how does it actually work?" or "what's running where?"

The dashboard is already showing it: four jobs on the EXPANSO
PLATFORM tile, the tier strip showing EDGE/FUSION/CLOUD, the Cloud
Egress tile with the running S3 archive pipeline, the topology
canvas showing sensor → fusion-node arrows.

If asked, walk through:

- **The four jobs**, all the same kind of Expanso job spec:
  `fusion-node` (this dashboard's backend, runs on the laptop),
  `sensor-north` and `sensor-south` (YOLO + Gemini cascade on the
  Jetson), `armyx-tech-event-archive` (the cloud-side Bloblang
  pipeline that ships every signed event to S3 with offline buffer).
- **The control plane**: Expanso Cloud at `cloud.expanso.io`. The
  same UI used to start jobs in the demo, edit pipelines mid-DDIL,
  and push the trigger config. Open the second browser tab.
- **The DBOM signatures** in event cards — every event signed at
  the edge so provenance survives the offline window.
- **Why this matters**: same architectural pattern works for an RF
  sensor (replace YOLO with classifier, replace camera tile with
  spectrum waterfall), for a maritime sensor, for any edge ISR
  modality. The substrate doesn't change.

If a judge asks the deploy question ("how do you get the jobs
running?") you can demonstrate live: open the Expanso Cloud UI,
stop one of the workload jobs (e.g. `sensor-north`), watch the
dashboard go to 3/4. Then click Start in the UI, watch it come
back to 4/4. That's the same control plane managing the demo's
state in real time.

---

## Time budget (target 4:00, hard ceiling 4:30)

| Beat | Target | Cumulative | Drop priority |
|------|--------|------------|---------------|
| Beat 1 — the pain | 0:45 | 0:45 | keep |
| Beat 2 — wishlist | 0:45 | 1:30 | keep |
| Beat 3 — solution in action (items 1-3) | 1:15 | 2:45 | keep |
| Beat 4 — fleet adapts (item 4) | 0:40 | 3:25 | 1st (drop if behind here, fold "fleet adapts" into the punchline) |
| Beat 5 (A+B+C+D) — surviving reality (item 5) | 1:30 | 4:55 | keep |
| Beat 6 — punchline | 0:30 | 5:25 | **never drop** |
| Beat 7 — deep tech tour | (Q&A only) | — | not in budget |

5:25 is over the 4:30 ceiling. **Two trim levers** depending on
rehearsal data:

- **Trim 1 (most flexible)**: Beat 5A narration tightens from 0:25
  → 0:15 (the "private link" framing can become one sentence) and
  Beat 5D from 0:15 → 0:10. Saves 0:15.
- **Trim 2**: Beat 1 from 0:45 → 0:30 if the room is small enough
  that the pain lands faster (drop the "four costs" enumeration,
  just say "four ways naive shipping kills you"). Saves 0:15.

With both trims: 4:55 cumulative. With Beat 4 dropped (fold its
content into Beat 6): 4:15. Always-budget for ~10s of unscripted
beats between sections (transitions take real time on stage).

### Drop-rule (live, on stage)

- If you hit **2:45** cumulative and haven't started Beat 4: skip
  Beat 4, fold "fleet adapts in seconds" as a one-line callout in
  Beat 6's punchline ("…sub-second fleet config push, zero-loss
  DDIL survival…").
- If you hit **4:00** and haven't done DDIL drain (Beat 5C): cut
  Beat 5D entirely, narrate the drain, jump to Beat 6.
- The punchline (Beat 6) absolutely cannot be cut — it's where the
  Expanso credit lands and where transferability becomes the
  takeaway.

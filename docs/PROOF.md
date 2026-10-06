# Proof report: 2026-10-05

All five public-bar criteria pass for this repository at commit
`2e396f9ad490da05bad43a5e4598ed343936b19f`. The check ran in a container that
mirrors `.github/workflows/public-bar.yml` (Ubuntu, Expanso Edge and CLI
v2.1.21 from the signed installers, Python 3.12.13, Playwright 1.55.0 with
Chromium, axe-core 4.10.3). Re-run it with the commands in the README.

| Criterion | Result | What proves it |
|---|---|---|
| 1. Runs | PASS | All nine jobs validate with `expanso-edge validate` and `expanso-cli job validate --offline`, and each replays its recorded input through a local Expanso Edge agent to exactly its expected output (`fixtures/<pipeline>/expected.jsonl`). |
| 2. Platform | PASS | AWS is declared as a managed-cloud claim with `event-archive` as its credential-free local replay. Beyond the check, `scripts/prove-s3.py` ran the real archive job against a TLS, per-identity S3 service (below). |
| 3. Structure | PASS | `public/guide/index.html` has the explanation, the step explorer with real per-stage input and output for all 39 stages of 9 pipelines, and the run and deploy sections. |
| 4. Usability | PASS | Rendered in Chromium: light default and dark toggle, axe and AA contrast, no horizontal overflow at 320, 400, 768 and 1440 px, Left and Right paging with a stable scroll anchor, vertical JSON, copy and download success and forced-failure feedback beside each control. |
| 5. Regressions | PASS | `public-features.json` retains every feature in the history audit; `public-removals/none.json` records that no removal was approved. |

Selftest of the vendored checker (`--selftest`):

```
PASS known-good: criteria 1-5 checks accepted the fixture
PASS service-pipeline: criteria 1-5 checks accepted the fixture
PASS criterion 1 Runs: failed alone and was named
PASS criterion 2 Platform: failed alone and was named
PASS criterion 3 Structure: failed alone and was named
PASS criterion 4 Usability: failed alone and was named
PASS criterion 5 Regressions: failed alone and was named
ok: public-bar selftest (2 good, 5 criterion-isolated failures)
```

## Archive output to S3

`scripts/prove-s3.py` starts a throwaway S3-compatible service (SeaweedFS 4.48,
pinned by digest) with TLS and per-identity permissions on a private Docker
network, then runs `jobs/event-archive-job.yaml` unchanged over
`fixtures/event-archive/input.jsonl` in the pinned Expanso Edge container with
the standard AWS environment variables. Result (`docs/s3-proof.json`):

```json
{
  "objects": 82,
  "expected_objects": 82,
  "sample_key": "events/sensor-north/20261006T034423.834Z-6bd9c684-c65c-436d-acd5-05842b7c3a98.json",
  "all_have_archive_time": true,
  "all_signed": true,
  "wrong_secret_refused": "SignatureDoesNotMatch",
  "writer_cannot_list": "AccessDenied",
  "writer_cannot_write_outside_events": "AccessDenied",
  "plaintext_http": "refused (HTTPError)"
}
```

Every event arrived as its own object, each carrying the archive time and the
signature audit. A wrong secret key was refused, the archive key could neither
list the bucket nor write outside `events/`, and plaintext HTTP to the TLS port
was refused. Running the job with its outputs kept also caught a defect that
`expanso-edge validate` cannot: two output processors shared a label, which
stops the pipeline from starting. It is fixed.

## Dashboards and pages

Measured with `tests/browser/page-audit.js` (document scroll width equal to the
viewport, no element sticking out, WCAG 2.x text contrast at AA) in light and
dark at 320, 400, 768 and 1440 px:

- Edge ISR dashboard: Ops, Architecture and Archive, 24 of 24 combinations with
  zero overflow and zero contrast failures; the S3 modal at 320 px; axe reports
  zero violations.
- Box pages (dashboard, architecture, live viewer, recordings viewer): 32 of 32.
- Guide: 8 of 8, and 4 of 4 widths again inside the check's rendered lane.

## Decisions for the captain

Features that earlier commits removed and that this work did not restore,
because restoring them contradicts the later redesign that routes model work
through the demo gateway (commit `0485c6d`, 2026-10-02):

- `sensor-gemini-frame-vision`: the sensor sent the camera frame to a hosted
  vision model. The gateway now sees detection labels only.
- `claude-relabel-drones`: `scripts/claude_relabel_drones.py` recovered drone
  training boxes with a command-line model. Its consumers
  (`ingest_relabel_to_3class.py`, `normalize_relabel_labels.py`) remain.
- `dataset-validate-claude`, `dataset-label-gemini`, `dataset-models-cmd`:
  `esc-dataset` labelling and validation used hosted vision models; they now use
  local YOLO-World and review images.
- `internal-runbooks-and-ops-scripts` (commit `dad856f`): private by design.

Closed: the architecture page's hardware price table now cites a public list
price for every line (computer $749, two cameras $199.98, switch $52.99; total
$1,002), with URLs and the 2026-10-05 access date in `docs/RESEARCH.md`. The
old `$866` total had no source and is gone.

Operator note: the event-specific environment variables for the S3 bucket,
Jetson host, AWS region and AWS profile are now `EDGE_ISR_S3_BUCKET`,
`EDGE_ISR_JETSON_HOST`, `EDGE_ISR_AWS_REGION` and `EDGE_ISR_AWS_PROFILE`. Rename
them in any local `.env`. Earlier commits still contain the old bucket name;
rewriting a public history needs a decision.

## Full public-bar report

- Generated: 2026-10-06T03:45:17Z
- Commit: `2e396f9ad490da05bad43a5e4598ed343936b19f`
- Checker: `1.1.3`
- Lane: `all`

## Result

| Criterion | Status |
|---|---|
| 1. Runs | PASS |
| 2. Platform | PASS |
| 3. Structure | PASS |
| 4. Usability | PASS |
| 5. Regressions | PASS |

## Tool versions

- `expanso-cli`: Expanso CLI version v2.1.21
- `expanso-edge`: v2.1.21
- `playwright`: 1.55.0
- `python`: 3.12.13

## Assertions

| Criterion | Assertion | Result | Detail |
|---|---|---|---|
| 1 | `manifest-schema` | PASS | public-bar.toml matches schema version 1 |
| 1 | `yaml-inventory` | PASS | classified 23 YAML files |
| 1 | `sensor-north:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `sensor-north:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `sensor-north:fixture` | PASS | local Edge replay matched 41 JSON records |
| 1 | `sensor-north:fixture:actual_sha256` | EVIDENCE | `d612e7073e9b050c8a0a903ffe347186cb23d6e56914a9d77593aa81c5954263` |
| 1 | `sensor-north:fixture:expected_sha256` | EVIDENCE | `d612e7073e9b050c8a0a903ffe347186cb23d6e56914a9d77593aa81c5954263` |
| 1 | `sensor-north:fixture:input_sha256` | EVIDENCE | `204824bc5578d56dcf1bc7a4264c99d845c0c273a89a7088f0e454c52db597cd` |
| 1 | `sensor-south:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `sensor-south:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `sensor-south:fixture` | PASS | local Edge replay matched 41 JSON records |
| 1 | `sensor-south:fixture:actual_sha256` | EVIDENCE | `69408bd554a283a95ec9e0cb52ae8a93c8005e81391307e13ec0c6f285e78c1e` |
| 1 | `sensor-south:fixture:expected_sha256` | EVIDENCE | `69408bd554a283a95ec9e0cb52ae8a93c8005e81391307e13ec0c6f285e78c1e` |
| 1 | `sensor-south:fixture:input_sha256` | EVIDENCE | `148e8bb08b7e52262288399959958ba2e17d9b93a8976f6d2852b072c372d817` |
| 1 | `fusion-node:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `fusion-node:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `fusion-node:fixture` | PASS | local Edge replay matched 83 JSON records |
| 1 | `fusion-node:fixture:actual_sha256` | EVIDENCE | `4a0686537e498f7aacd0be2dce32e93c58302a80fa347ffeea48ad7d1d4c7db8` |
| 1 | `fusion-node:fixture:expected_sha256` | EVIDENCE | `4a0686537e498f7aacd0be2dce32e93c58302a80fa347ffeea48ad7d1d4c7db8` |
| 1 | `fusion-node:fixture:input_sha256` | EVIDENCE | `31bb113fbedbf72e390b9d48027a6ba9c086ef8b0fd7d5800897990abff22a0d` |
| 1 | `fuse:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `fuse:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `fuse:fixture` | PASS | local Edge replay matched 1 JSON records |
| 1 | `fuse:fixture:actual_sha256` | EVIDENCE | `0a7f6e8a9a96fde758d168db5482164d48681e7b2499781126ed7bc78d770cc8` |
| 1 | `fuse:fixture:expected_sha256` | EVIDENCE | `0a7f6e8a9a96fde758d168db5482164d48681e7b2499781126ed7bc78d770cc8` |
| 1 | `fuse:fixture:input_sha256` | EVIDENCE | `c2ff953b4e343e7df910e8db22cefa441091cf7fe7ae02a82cd2b24ab1579c3a` |
| 1 | `event-archive:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `event-archive:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `event-archive:fixture` | PASS | local Edge replay matched 82 JSON records |
| 1 | `event-archive:fixture:actual_sha256` | EVIDENCE | `d388895b664c4ca9fa270473184289a554b247a20b2efcf93918591f8c73f824` |
| 1 | `event-archive:fixture:expected_sha256` | EVIDENCE | `d388895b664c4ca9fa270473184289a554b247a20b2efcf93918591f8c73f824` |
| 1 | `event-archive:fixture:input_sha256` | EVIDENCE | `6204584b180e0cca9a9931846bd83649c2af7382aff61a47f201af910f58fe9d` |
| 1 | `yolo-detector:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `yolo-detector:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `yolo-detector:fixture` | PASS | local Edge replay matched 21 JSON records |
| 1 | `yolo-detector:fixture:actual_sha256` | EVIDENCE | `82fd9c1def5ea096c45491601903e6483fc3430a09ccccefea6181b8d7854d17` |
| 1 | `yolo-detector:fixture:expected_sha256` | EVIDENCE | `82fd9c1def5ea096c45491601903e6483fc3430a09ccccefea6181b8d7854d17` |
| 1 | `yolo-detector:fixture:input_sha256` | EVIDENCE | `1124323a6e2ef80154b1210bb23bbb0991f407f5454313ebdf280460a3ea83e3` |
| 1 | `security-camera-events:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `security-camera-events:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `security-camera-events:fixture` | PASS | local Edge replay matched 21 JSON records |
| 1 | `security-camera-events:fixture:actual_sha256` | EVIDENCE | `306b60dee6f67a925bcf79bdb48a13cbf93b3887907ccee223fa49dc0cbf6f47` |
| 1 | `security-camera-events:fixture:expected_sha256` | EVIDENCE | `306b60dee6f67a925bcf79bdb48a13cbf93b3887907ccee223fa49dc0cbf6f47` |
| 1 | `security-camera-events:fixture:input_sha256` | EVIDENCE | `870378da8b894f7a891df73d68ab499ca637a59a3554a548a25a2f4fb3549b8a` |
| 1 | `box-crossing:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `box-crossing:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `box-crossing:fixture` | PASS | local Edge replay matched 10 JSON records |
| 1 | `box-crossing:fixture:actual_sha256` | EVIDENCE | `1b9294e9a707a68c9bdc3444d36ea647d154a638df53a244a15c0881149fd769` |
| 1 | `box-crossing:fixture:expected_sha256` | EVIDENCE | `1b9294e9a707a68c9bdc3444d36ea647d154a638df53a244a15c0881149fd769` |
| 1 | `box-crossing:fixture:input_sha256` | EVIDENCE | `1a1ed2dc0953e0d32b7785d1aa38a45646d590d26a74764e7b959fbb6be67aae` |
| 1 | `security-camera-recorder:edge-validate` | PASS | expanso-edge validate passed |
| 1 | `security-camera-recorder:job-validate` | PASS | expanso-cli offline job validation passed |
| 1 | `security-camera-recorder:fixture` | PASS | local Edge replay matched 8 JSON records |
| 1 | `security-camera-recorder:fixture:actual_sha256` | EVIDENCE | `e223d878bf764f6565d44b371c98e7a77e5adc4b6c53499c27e245b671578fa1` |
| 1 | `security-camera-recorder:fixture:expected_sha256` | EVIDENCE | `e223d878bf764f6565d44b371c98e7a77e5adc4b6c53499c27e245b671578fa1` |
| 1 | `security-camera-recorder:fixture:input_sha256` | EVIDENCE | `f888a48d6e49dd5e1fe74439123b1a65e13edaed6270df9ea4fd0b67cdcaaaeb` |
| 2 | `platform-inventory` | PASS | declared 1 named platforms |
| 2 | `aws` | PASS | managed-cloud files and integration declaration passed |
| 3 | `explanation` | PASS | #explanation matched 1 element(s) |
| 3 | `explorer` | PASS | #explorer matched 1 element(s) |
| 3 | `run` | PASS | #run matched 1 element(s) |
| 3 | `deploy` | PASS | #deploy matched 1 element(s) |
| 3 | `stage:sensor-north-parse-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-north-validate-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-north-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-north-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-south-parse-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-south-validate-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-south-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:sensor-south-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fusion-node-parse-line` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fusion-node-classify` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fusion-node-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fusion-node-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fuse-parse-alert` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fuse-keep-fused-alerts` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fuse-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fuse-sign-record` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:fuse-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:event-archive-parse-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:event-archive-keep-sensor-events` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:event-archive-check-signature` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:event-archive-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:event-archive-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:yolo-detector-parse-scan` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:yolo-detector-add-source` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:yolo-detector-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-events-parse-scan` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-events-require-schema` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-events-add-source` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-events-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-events-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-parse-event` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-require-schema` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-add-source` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-normalise-class` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-add-lineage` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:box-crossing-deliver` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-recorder-parse-segment` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-recorder-add-source` | PASS | fixture-backed explorer stage exists |
| 3 | `stage:security-camera-recorder-deliver` | PASS | fixture-backed explorer stage exists |
| 4 | `browser-contract` | PASS | rendered-browser declarations are complete |
| 4 | `source-policy` | PASS | source avoids banned public-demo decoration |
| 5 | `feature-manifest` | PASS | public-features.json matches schema version 1 |
| 5 | `baseline` | PASS | initial adoption: no prior retained-feature baseline exists |
| 4 | `theme-default:320` | PASS | default theme reports light |
| 4 | `overflow:320:light` | PASS | scrollWidth 320 <= clientWidth 320 |
| 4 | `contrast:320:light` | PASS | all visible text meets WCAG AA |
| 4 | `axe:320:light` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-dark:320` | PASS | toggle reports dark |
| 4 | `overflow:320:dark` | PASS | scrollWidth 320 <= clientWidth 320 |
| 4 | `contrast:320:dark` | PASS | all visible text meets WCAG AA |
| 4 | `axe:320:dark` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-default:400` | PASS | default theme reports light |
| 4 | `overflow:400:light` | PASS | scrollWidth 400 <= clientWidth 400 |
| 4 | `contrast:400:light` | PASS | all visible text meets WCAG AA |
| 4 | `axe:400:light` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-dark:400` | PASS | toggle reports dark |
| 4 | `overflow:400:dark` | PASS | scrollWidth 400 <= clientWidth 400 |
| 4 | `contrast:400:dark` | PASS | all visible text meets WCAG AA |
| 4 | `axe:400:dark` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-default:768` | PASS | default theme reports light |
| 4 | `overflow:768:light` | PASS | scrollWidth 768 <= clientWidth 768 |
| 4 | `contrast:768:light` | PASS | all visible text meets WCAG AA |
| 4 | `axe:768:light` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-dark:768` | PASS | toggle reports dark |
| 4 | `overflow:768:dark` | PASS | scrollWidth 768 <= clientWidth 768 |
| 4 | `contrast:768:dark` | PASS | all visible text meets WCAG AA |
| 4 | `axe:768:dark` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-default:1440` | PASS | default theme reports light |
| 4 | `overflow:1440:light` | PASS | scrollWidth 1440 <= clientWidth 1440 |
| 4 | `contrast:1440:light` | PASS | all visible text meets WCAG AA |
| 4 | `axe:1440:light` | PASS | axe found no WCAG A/AA violations |
| 4 | `theme-dark:1440` | PASS | toggle reports dark |
| 4 | `overflow:1440:dark` | PASS | scrollWidth 1440 <= clientWidth 1440 |
| 4 | `contrast:1440:dark` | PASS | all visible text meets WCAG AA |
| 4 | `axe:1440:dark` | PASS | axe found no WCAG A/AA violations |
| 4 | `stage-keys` | PASS | Right and Left paged 39 stages; scroll stayed within 2px |
| 4 | `json:[data-public-json]:0` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:1` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:2` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:3` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:4` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:5` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:6` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:7` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:8` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:9` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:10` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:11` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:12` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:13` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:14` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:15` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:16` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:17` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:18` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:19` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:20` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:21` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:22` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:23` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:24` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:25` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:26` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:27` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:28` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:29` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:30` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:31` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:32` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:33` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:34` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:35` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:36` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:37` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:38` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:39` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:40` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:41` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:42` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:43` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:44` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:45` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:46` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:47` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:48` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:49` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:50` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:51` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:52` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:53` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:54` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:55` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:56` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:57` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:58` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:59` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:60` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:61` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:62` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:63` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:64` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:65` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:66` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:67` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:68` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:69` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:70` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:71` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:72` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:73` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:74` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:75` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:76` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:77` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:78` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:79` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:80` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:81` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:82` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:83` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:84` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:85` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:86` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:87` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:88` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:89` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:90` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:91` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:92` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:93` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:94` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:95` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:96` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:97` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:98` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:99` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:100` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:101` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:102` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:103` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:104` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:105` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:106` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:107` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:108` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:109` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:110` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:111` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:112` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:113` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:114` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:115` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:116` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:117` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:118` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:119` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:120` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:121` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:122` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:123` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:124` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:125` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:126` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:127` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:128` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:129` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:130` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:131` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:132` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:133` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:134` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:135` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:136` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:137` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:138` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:139` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:140` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:141` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:142` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:143` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:144` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:145` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:146` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:147` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:148` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:149` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:150` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:151` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:152` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:153` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:154` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:155` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:156` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:157` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:158` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:159` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:160` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:161` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:162` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:163` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:164` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:165` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:166` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:167` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:168` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:169` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:170` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:171` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:172` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:173` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:174` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:175` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:176` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:177` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:178` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:179` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:180` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:181` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:182` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:183` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:184` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:185` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:186` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:187` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:188` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:189` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:190` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:191` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:192` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:193` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:194` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:195` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:196` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:197` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:198` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:199` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:200` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:201` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:202` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:203` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:204` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:205` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:206` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:207` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:208` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:209` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:210` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:211` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:212` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `json:[data-public-json]:213` | PASS | JSON is valid and vertically pretty-printed |
| 4 | `control:copy-stage-yaml` | PASS | copy invoked=True, local success=True, forced failure=True |
| 4 | `control:copy-stage-output` | PASS | copy invoked=True, local success=True, forced failure=True |
| 4 | `control:download-stage-output` | PASS | download completed=True, local success=True, forced failure=True |
| 4 | `control:copy-run-install` | PASS | copy invoked=True, local success=True, forced failure=True |
| 4 | `control:copy-deploy-jobs` | PASS | copy invoked=True, local success=True, forced failure=True |
| 4 | `control:download-job` | PASS | download completed=True, local success=True, forced failure=True |
| 4 | `teardown` | PASS | all declared services and browser hosts stopped |

# demo-security-camera — MVP Implementation Plan

## Solo Build Playbook · Target: 2 Weeks to Working Demo

---

## The Goal in One Sentence

Two cameras, one Jetson, two recorded demos (dock door + people counting), a live-runnable stack, and a showcase page — proving Expanso runs a complete edge video analytics pipeline on a $249 device.

---

## What "Done" Looks Like

By end of week 2, you have:

1. **A GitHub repo** (`expanso-io/demo-security-camera`) with a working `docker-compose up` that runs the full pipeline
2. **A recorded dock door demo** (2–3 min video) showing two cameras counting boxes through a doorway, reconciling counts, flagging a deliberate discrepancy — ready to send to Joe/Array
3. **A recorded people counting demo** (2–3 min video) showing person counting at two entrances — for the showcase page and broader sales conversations
4. **A live demo** you can run on the Jetson in a meeting — plug in, `docker-compose up`, point at dashboard, walk past camera, count increments
5. **A static showcase page** with annotated frames, count charts, and the cost comparison story

---

## What to Cut for MVP (Compared to the Full Spec)

The full spec describes the production architecture. Here's what the MVP does NOT need:

| Full Spec Feature | MVP Status | Why |
|---|---|---|
| GStreamer/DeepStream pipeline | **Cut — use FFMPEG/PyAV** | DeepStream is the right production choice but adds 2–3 days of setup complexity. PyAV with OpenCV and direct TensorRT inference gets you to "working" faster. Add DeepStream in v1.1. |
| Pip-installable Python package | **Cut — just a repo with scripts** | Package structure is nice but adds overhead. A clean repo with `python run.py` and `docker-compose up` is enough for the demo. |
| Flask + HTMX dashboard | **Simplify — use Streamlit** | Streamlit gets you a functional dashboard in 2 hours vs. 2 days for Flask + templates. It's not production-grade but it's demo-grade. |
| Zone polygon editor UI | **Cut — hardcode zone coords** | You know where the cameras are pointed. Hardcode the counting line coordinates in config. No UI needed. |
| OTA model updates | **Cut entirely** | Nice for production, irrelevant for demo. |
| Offline buffer flush logic | **Simplify — demonstrate with a log** | Show that Expanso buffers events when you unplug ethernet. Don't build sophisticated replay logic. |
| Multiple YAML config profiles | **Cut — one config file** | Swap `detect_classes` manually between recordings. No profile system needed. |
| RTSP simulator container | **Cut for MVP** | Use real cameras. The simulator is for people running the demo without hardware — build it after the demo works. |

**The MVP architecture simplifies to:**

```
Camera 1 (RTSP) ──┐
                   ├──▶ Python script (PyAV decode → YOLOv8 TensorRT → ByteTrack → count logic)
Camera 2 (RTSP) ──┘                                    │
                                                        ▼
                                                 Expanso Agent
                                                   │        │
                                                   ▼        ▼
                                             Streamlit    Upstream
                                             Dashboard    HTTP sink
```

One Python process. Two threads for stream ingest. Batched inference on GPU. Counts fed to Expanso. Expanso feeds the dashboard. That's it.

---

## Hardware Setup Checklist

Do this first. Before writing a single line of code, get the physical setup working.

### Jetson Orin Nano

- [ ] Flash JetPack 6.1 (SD card image from NVIDIA)
- [ ] Boot, connect to network (ethernet preferred for stability)
- [ ] SSH access working
- [ ] Run `sudo nvpmodel -m 2` then `sudo jetson_clocks` (unlock Super performance mode)
- [ ] Verify: `nvcc --version` shows CUDA 12.x
- [ ] Verify: `dpkg -l | grep TensorRT` shows TensorRT 10.x
- [ ] Install NVMe SSD if you have one (not required but helps with TensorRT engine caching)

### Cameras

- [ ] Two IP cameras powered and on the same network as the Jetson
- [ ] Find each camera's RTSP URL (typically `rtsp://<ip>:554/stream1` — check camera's web UI)
- [ ] Test from Jetson: `ffplay rtsp://<camera-ip>:554/stream1` — you should see live video
- [ ] Note both RTSP URLs in a text file. You'll need them constantly.

**If cameras give you trouble:** Many cheap IP cameras have RTSP disabled by default or use non-standard paths. Check the manufacturer's documentation. Reolink cameras use `rtsp://<ip>:554/h264Preview_01_main`. Amcrest use `rtsp://<ip>:554/cam/realmonitor?channel=1&subtype=0`. Hikvision use `rtsp://<ip>:554/Streaming/Channels/101`.

### Physical Demo Space (Plan This Now)

**Dock door demo setup:**
- A doorway, garage opening, or any threshold you can carry boxes through
- Camera 1: mounted/tripoded on the "outside" facing the threshold
- Camera 2: mounted/tripoded on the "inside" facing the threshold
- Both cameras should see the full width of the doorway
- 10–15 cardboard boxes of varying sizes (Amazon boxes are perfect)
- Staging area on each side to stack boxes

**People counting setup (can be same location or different):**
- A hallway, entrance, or open area with two distinct entry points
- Camera 1: watching entrance A
- Camera 2: watching entrance B
- You + 1–2 other people to walk through (ask a family member, coworker, anyone)

---

## Day-by-Day Execution Plan

### Day 1: Jetson Environment + Camera Verification

**Morning: Jetson setup**

```bash
# After JetPack 6.1 is flashed and booted:
sudo nvpmodel -m 2
sudo jetson_clocks

# Create working directory
mkdir -p ~/demo-security-camera && cd ~/demo-security-camera

# Python environment
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv
python3 -m venv venv --system-site-packages  # --system-site-packages picks up CUDA/TensorRT
source venv/bin/activate

# Core dependencies
pip install av opencv-python-headless numpy ultralytics pydantic pyyaml

# Verify CUDA is accessible from Python
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

**Afternoon: Camera streams working**

```bash
# Test each camera (replace with your actual RTSP URLs)
# If ffplay isn't available: sudo apt-get install ffmpeg
ffplay -rtsp_transport tcp rtsp://192.168.1.100:554/stream1
ffplay -rtsp_transport tcp rtsp://192.168.1.101:554/stream1

# Test pulling frames with Python (quick sanity check)
python3 -c "
import av
container = av.open('rtsp://192.168.1.100:554/stream1', options={'rtsp_transport': 'tcp'})
stream = container.streams.video[0]
for i, frame in enumerate(container.decode(stream)):
    print(f'Frame {i}: {frame.width}x{frame.height}')
    if i > 10: break
"
```

**Done when:** Both cameras stream video to the Jetson reliably. You can pull 30+ frames from each without errors.

---

### Day 2: YOLOv8 TensorRT Inference Working

**Morning: Build TensorRT engine**

```bash
cd ~/demo-security-camera
source venv/bin/activate

# Download YOLOv8s and export to TensorRT
python3 -c "
from ultralytics import YOLO
model = YOLO('yolov8s.pt')
model.export(format='engine', half=True, imgsz=640, batch=2)  # batch=2 for two cameras
print('Engine built successfully')
"
# This takes 10-20 minutes. Go get coffee.
# Output: yolov8s.engine in current directory
```

**Afternoon: Single-stream inference test**

```python
# test_single_stream.py
import av
import cv2
import numpy as np
from ultralytics import YOLO

model = YOLO('yolov8s.engine')  # Load TensorRT engine

container = av.open('rtsp://192.168.1.100:554/stream1', options={'rtsp_transport': 'tcp'})
stream = container.streams.video[0]

for i, frame in enumerate(container.decode(stream)):
    img = frame.to_ndarray(format='bgr24')
    results = model(img, verbose=False)
    
    # Draw boxes
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            label = f"{model.names[cls]} {conf:.2f}"
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(img, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    cv2.imwrite(f'test_frames/frame_{i:04d}.jpg', img)
    
    if i % 30 == 0:
        print(f"Frame {i}: {len(results[0].boxes)} detections, inference {results[0].speed['inference']:.1f}ms")
    
    if i > 300:  # ~10 seconds
        break
```

```bash
mkdir -p test_frames
python3 test_single_stream.py
# Check test_frames/ — you should see annotated frames with bounding boxes
```

**Done when:** You see bounding boxes around detected objects in the saved frames. Inference time should be 20–30ms per frame for YOLOv8s FP16.

---

### Day 3: Two Streams + ByteTrack

**Morning: Multi-stream threaded ingest**

```python
# dual_stream.py — the core of the MVP
import av
import cv2
import numpy as np
import threading
import queue
import time
from ultralytics import YOLO

STREAMS = {
    'camera-outside': 'rtsp://192.168.1.100:554/stream1',
    'camera-inside': 'rtsp://192.168.1.101:554/stream1',
}

frame_queues = {name: queue.Queue(maxsize=5) for name in STREAMS}

def stream_reader(name, url, q):
    """Read frames from RTSP stream in a thread."""
    while True:
        try:
            container = av.open(url, options={'rtsp_transport': 'tcp'})
            stream = container.streams.video[0]
            for frame in container.decode(stream):
                img = frame.to_ndarray(format='bgr24')
                if q.full():
                    try:
                        q.get_nowait()  # Drop oldest frame
                    except queue.Empty:
                        pass
                q.put((time.time(), img))
        except Exception as e:
            print(f"[{name}] Stream error: {e}, reconnecting in 5s...")
            time.sleep(5)

# Start stream reader threads
for name, url in STREAMS.items():
    t = threading.Thread(target=stream_reader, args=(name, url, frame_queues[name]), daemon=True)
    t.start()

# Load model
model = YOLO('yolov8s.engine')

print("Waiting for streams...")
time.sleep(3)

frame_count = 0
while True:
    frames = {}
    for name, q in frame_queues.items():
        try:
            ts, img = q.get(timeout=1.0)
            frames[name] = (ts, img)
        except queue.Empty:
            continue
    
    if not frames:
        continue
    
    # Batch inference on all available frames
    images = [img for _, img in frames.values()]
    results = model(images, verbose=False)
    
    for (name, (ts, img)), result in zip(frames.items(), results):
        n_dets = len(result.boxes)
        if frame_count % 30 == 0:
            print(f"[{name}] {n_dets} detections, {result.speed['inference']:.0f}ms inference")
    
    frame_count += 1
```

**Afternoon: Add ByteTrack**

Ultralytics has ByteTrack built in — you just pass `track=True`:

```python
# Replace model() call with model.track() — that's the entire change
results = model.track(images, verbose=False, persist=True, tracker='bytetrack.yaml')

# Now each detection has a track ID:
for box in result.boxes:
    track_id = int(box.id[0]) if box.id is not None else -1
```

Test this by walking past one camera repeatedly. You should see the same `track_id` assigned to you across consecutive frames, and a new ID when you leave and re-enter.

**Done when:** Two streams running simultaneously with tracked bounding boxes. Each detected object has a stable track ID within its stream.

---

### Day 4: Line-Crossing Counter

**Morning: Implement counting logic**

This is the key piece that turns "detection" into "useful data." A counting line is defined by two points in frame coordinates. When a tracked object's centroid crosses the line, increment the count.

```python
# counter.py
import numpy as np

class LineCrossingCounter:
    """Counts objects crossing a defined line, with direction awareness."""
    
    def __init__(self, line_start, line_end):
        self.line_start = np.array(line_start, dtype=float)
        self.line_end = np.array(line_end, dtype=float)
        self.previous_positions = {}  # track_id -> previous centroid
        self.in_count = 0
        self.out_count = 0
        self.counted_ids = set()  # Don't double-count same track
    
    def _side_of_line(self, point):
        """Returns positive or negative depending on which side of the line the point is."""
        d = (point[0] - self.line_start[0]) * (self.line_end[1] - self.line_start[1]) - \
            (point[1] - self.line_start[1]) * (self.line_end[0] - self.line_start[0])
        return d
    
    def update(self, track_id, centroid):
        """Check if track_id has crossed the line since last update."""
        centroid = np.array(centroid, dtype=float)
        
        if track_id in self.counted_ids:
            self.previous_positions[track_id] = centroid
            return None  # Already counted this track
        
        if track_id in self.previous_positions:
            prev = self.previous_positions[track_id]
            prev_side = self._side_of_line(prev)
            curr_side = self._side_of_line(centroid)
            
            if prev_side * curr_side < 0:  # Crossed the line
                self.counted_ids.add(track_id)
                if curr_side > 0:
                    self.in_count += 1
                    self.previous_positions[track_id] = centroid
                    return 'in'
                else:
                    self.out_count += 1
                    self.previous_positions[track_id] = centroid
                    return 'out'
        
        self.previous_positions[track_id] = centroid
        return None
    
    def get_counts(self):
        return {'in': self.in_count, 'out': self.out_count, 'total': self.in_count + self.out_count}
    
    def reset(self):
        self.in_count = 0
        self.out_count = 0
        self.counted_ids.clear()
        self.previous_positions.clear()
```

**Afternoon: Integrate counter with dual-stream pipeline**

Wire the counter into the main loop. Each camera gets its own `LineCrossingCounter`. After each inference + tracking step, feed centroids to the counter. Print count updates to the console.

**Calibrating the counting line:** Point each camera at the doorway. Take a screenshot. Open it in any image editor. Note the pixel coordinates of a horizontal line across the doorway threshold. Those coordinates go into your config.

```yaml
# config.yaml
streams:
  camera-outside:
    url: "rtsp://192.168.1.100:554/stream1"
    counting_line:
      start: [50, 350]   # Pixel coords — calibrate to your camera's view
      end: [590, 350]
  camera-inside:
    url: "rtsp://192.168.1.101:554/stream1"
    counting_line:
      start: [50, 300]
      end: [590, 300]

detect_classes: [39, 41, 42, 43]  # COCO: bottle, cup, bowl, banana — OR whatever works for boxes
# For people counting, swap to: [0]  # COCO: person
```

**Done when:** Walk past each camera carrying a box. Console prints "camera-outside: IN count = 1" and "camera-inside: IN count = 1". Counts increment correctly and don't double-count.

---

### Day 5: Expanso Agent Integration

**Morning: Get Expanso agent running on Jetson**

```bash
# Install Expanso agent (confirm exact install steps with your eng docs)
# Assuming it's available as a binary or container for ARM64:
curl -sSL https://get.expanso.io | bash   # Or whatever the install path is

# Verify it's running
expanso agent status
```

**Afternoon: Wire count events into Expanso**

The integration point is simple: every time the counter registers a line crossing (or on a timed interval), serialize a count event and hand it to Expanso.

```python
# expanso_sink.py
import json
import time
import requests  # Or use Expanso's Python SDK if one exists
from datetime import datetime, timezone

class ExpansoSink:
    """Sends structured count events to Expanso agent."""
    
    def __init__(self, device_id, agent_url="http://localhost:1234"):
        self.device_id = device_id
        self.agent_url = agent_url
        self.buffer = []
    
    def emit_count(self, stream_id, counts, window_start, window_end):
        event = {
            "event_type": "count",
            "device_id": self.device_id,
            "stream_id": stream_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "counts": counts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._send(event)
    
    def _send(self, event):
        try:
            # Replace with actual Expanso agent API call
            # This might be a REST POST, a named pipe, a gRPC call, etc.
            # depending on how the Expanso agent accepts local events
            print(f"[Expanso] {json.dumps(event, indent=2)}")
            # requests.post(f"{self.agent_url}/events", json=event)
        except Exception as e:
            print(f"[Expanso] Send failed, buffering: {e}")
            self.buffer.append(event)
```

Integrate this into the main loop: every 30 seconds (configurable), emit a count event for each camera.

**Done when:** Expanso agent receives count events from the inference pipeline. You can see them in Expanso's logs or dashboard.

---

### Day 6: Streamlit Dashboard

**Morning: Build the dashboard**

```bash
pip install streamlit plotly
```

```python
# dashboard.py
import streamlit as st
import json
import time
from pathlib import Path

st.set_page_config(page_title="Wall-E Security Camera Demo", layout="wide")
st.title("🎥 Edge Video Analytics — Live Dashboard")

# Read events from a shared JSON file (simplest IPC for MVP)
# The inference pipeline writes events here; the dashboard reads them
EVENT_FILE = Path("events.jsonl")

@st.cache_data(ttl=2)  # Refresh every 2 seconds
def load_events():
    events = []
    if EVENT_FILE.exists():
        for line in EVENT_FILE.read_text().strip().split('\n'):
            if line:
                events.append(json.loads(line))
    return events

events = load_events()

col1, col2, col3 = st.columns(3)

# Camera 1 counts
cam1_events = [e for e in events if e.get('stream_id') == 'camera-outside']
cam1_total = cam1_events[-1]['counts']['total'] if cam1_events else 0
col1.metric("📦 Camera 1 (Outside)", f"{cam1_total} boxes")

# Camera 2 counts
cam2_events = [e for e in events if e.get('stream_id') == 'camera-inside']
cam2_total = cam2_events[-1]['counts']['total'] if cam2_events else 0
col2.metric("📦 Camera 2 (Inside)", f"{cam2_total} boxes")

# Delta / reconciliation
delta = abs(cam1_total - cam2_total)
if delta == 0:
    col3.metric("✅ Reconciliation", "Counts Match", delta_color="normal")
else:
    col3.metric("⚠️ Discrepancy", f"{delta} box difference", delta_color="inverse")

# Count over time chart
st.subheader("Count Over Time")
# Build a simple time series from events and plot with st.line_chart
# ... (flesh this out based on your actual event structure)

st.caption("Running on NVIDIA Jetson Orin Nano · Expanso Edge Pipeline · YOLOv8s TensorRT FP16")
```

```bash
streamlit run dashboard.py --server.port 8080
```

**Afternoon: Connect dashboard to live pipeline**

The simplest IPC for the MVP: the inference pipeline appends JSON lines to `events.jsonl`. The Streamlit dashboard reads and displays them with a 2-second refresh. This is janky but it works for a demo. In production, Expanso handles this properly.

**Done when:** Dashboard shows live counts from both cameras updating in real time. The reconciliation metric shows match/mismatch correctly.

---

### Day 7: Record the Dock Door Demo

This is filming day. Treat it like a product video shoot.

**Setup (30 min):**
- Mount cameras at the doorway — outside and inside views
- Stack 12–15 boxes on the "outside" staging area
- Open the dashboard on a laptop screen (pointing the Jetson's Streamlit at port 8080)
- Start screen recording on the laptop (OBS, QuickTime, or similar)
- Start the inference pipeline on the Jetson

**Recording script:**

1. **Shot 1 — The clean run (2 min)**
   - Show both camera views briefly (via VLC or the dashboard's annotated feed)
   - Carry boxes through the doorway one at a time, steady pace
   - Dashboard counts climbing in sync on both cameras
   - After all 12 boxes: dashboard shows 12/12, "Counts Match" ✅
   - Hold on the dashboard for 5 seconds — this is the money shot

2. **Shot 2 — The discrepancy run (2 min)**
   - Reset counts (restart the pipeline or add a reset button)
   - Carry 12 boxes toward the door, but set 2 aside before they cross camera 2's view
   - Outside camera: 12. Inside camera: 10.
   - Dashboard: "⚠️ 2 box discrepancy"
   - Hold on the dashboard — this is the SECOND money shot
   - This is literally the Wall-E pitch: "here's the problem we catch"

3. **Shot 3 — The hardware (30 sec)**
   - Quick pan of the physical setup: the Jetson, the cameras, the PoE switch
   - "This entire system costs $684"

**After recording:** Save raw footage AND the screen recording. You'll use the screen recording (dashboard + annotated frames) for sales calls, and pull still frames from the camera footage for the showcase page.

---

### Day 8: Record the People Counting Demo

**Setup:**
- Reposition cameras at two entrances/hallways (or keep the doorway and just change the angle)
- Edit config: `detect_classes: [0]` (COCO class 0 = person)
- Restart pipeline, verify person detection working

**Recording script:**
1. Walk through entrance A (camera 1 counts)
2. Walk through entrance B (camera 2 counts)
3. Have 2–3 people walk through both entrances in different patterns
4. Dashboard shows per-entrance counts over time
5. Total unique entries across site

Simpler than the dock door demo — this one is about showing the system is configurable and generalizes beyond boxes.

---

### Days 9–10: Showcase Page + Repo Cleanup + Docker

**Day 9 Morning: Build the showcase page**

Pull annotated frame screenshots from both demo recordings. Generate count-over-time charts from the recorded `events.jsonl` data. Build a single `index.html`:

- Hero: "Edge Video Analytics on a $249 Device"
- Before/after: "What a $10K NVR system does" vs. "What this does for $684"
- Annotated frames from both demos (dock door + people counting)
- Count charts
- Architecture diagram
- BOM table
- "Get Started: 3 commands"

**Day 9 Afternoon: Clean up the repo**

Organize code into the package structure from the spec. Write the README. Make sure `docker-compose up` works end-to-end. Add the config.yaml with both profiles (boxes and people).

**Day 10: Dockerize**

```dockerfile
# Dockerfile.jetson
FROM nvcr.io/nvidia/l4t-pytorch:r36.2.0-pth2.1-py3

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml .

CMD ["python3", "src/main.py", "--config", "config.yaml"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  inference:
    build:
      context: .
      dockerfile: Dockerfile.jetson
    runtime: nvidia
    network_mode: host  # Simplest way to access RTSP cameras on local network
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./models:/app/models  # Cached TensorRT engines
      - ./events:/app/events  # Event output
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    
  dashboard:
    build:
      context: .
      dockerfile: Dockerfile.jetson
    command: streamlit run src/dashboard.py --server.port 8080 --server.address 0.0.0.0
    ports:
      - "8080:8080"
    volumes:
      - ./events:/app/events  # Reads events written by inference
```

Add the RTSP simulator service for people without cameras:

```yaml
  rtsp-simulator:
    image: bluenviron/mediamtx:latest
    ports:
      - "8554:8554"
    volumes:
      - ./sample_data:/videos
    # Configure mediamtx to serve sample videos as RTSP streams
    # See mediamtx docs for path configuration
```

**Done when:** Fresh clone → `docker-compose up` → dashboard at :8080 showing live detection from cameras (or sample videos if no cameras).

---

## Detection Class Strategy for Boxes

YOLO's COCO classes don't include "cardboard box" specifically. Options:

**Option A (fastest, try first):** Configure to detect all objects crossing the line regardless of class. Set a generous confidence threshold (0.3). YOLO will likely detect boxes as some combination of "suitcase" (class 28), "backpack" (class 24), or "handbag" (class 26). For the demo, the class label doesn't matter — what matters is that the count increments when a box crosses the line.

**Option B (if A doesn't detect reliably):** Use a class-agnostic approach. Run YOLO, take ALL detections above confidence 0.25 that are within a certain size range (boxes are typically 50–200px on screen), and count those. This is hacky but effective for a controlled demo.

**Option C (if you have an afternoon to spare):** Fine-tune YOLOv8s on a small box dataset from Roboflow (there are several with ~500 images of shipping boxes). Ultralytics makes this trivial: `model.train(data='boxes.yaml', epochs=30)`. Gives you a model that confidently detects "box" as a labeled class. Looks better on video (bounding box says "box 0.94" instead of "suitcase 0.67").

**Recommendation:** Start with Option A. If boxes aren't detected reliably, jump to Option C — it takes 2 hours including the Roboflow download and fine-tuning, and the result is more convincing for the demo.

---

## Recording Tips (Seriously — This Matters for Sales)

The quality of the recorded demo will determine whether Joe watches it for 30 seconds or 3 minutes. A few things that make the difference:

**Lighting:** Natural daylight or bright overhead lights. Low light kills detection confidence. If you're using a garage, open the door fully.

**Camera angle:** Slightly above head height (7–8 feet), angled down 20–30 degrees. This is the standard security camera angle and ensures YOLO has a good view of the full body/box shape.

**Steady cameras:** Tripods or clamp mounts. Shaky cameras make the demo look amateur and confuse the tracker.

**Clean background:** Remove clutter from the camera's field of view where possible. A clean background means fewer false detections to explain away.

**Pace yourself:** Move boxes through at a steady, deliberate pace — one every 3–5 seconds. Too fast and the tracker might lose track; too slow and the demo drags. Watch the dashboard as you go; you want to see the count tick up with each box.

**The laptop screen recording is the deliverable.** The camera footage is supporting material. What Joe will actually watch is the dashboard counting boxes in real time with the annotated camera feeds in the corner. Make sure the dashboard is readable at screen-recording resolution.

---

## After the MVP: What to Build Next

Once the recorded demos exist and the live demo works, here's the priority order for follow-on work:

1. **RTSP simulator** — so people can run the demo without cameras (docker-compose.sample.yml)
2. **DeepStream pipeline** — replace PyAV with the proper GStreamer/DeepStream path for production performance numbers
3. **Proper Expanso event routing** — replace the JSONL file IPC with actual Expanso agent API calls
4. **Showcase page polish** — make it look professional, host it on GitHub Pages
5. **Config UI** — simple web page for defining counting lines and camera URLs (replaces hardcoded coords)
6. **Package as pip-installable** — pyproject.toml, CLI entrypoint, proper versioning

---

## Success Criteria

The demo is successful if:

- [ ] A non-technical person can watch the dock door video and understand "it counts boxes through a door and catches discrepancies"
- [ ] A technical person can clone the repo, run `docker-compose up`, and see live detection on the dashboard within 10 minutes
- [ ] The live demo works reliably enough to run in a meeting without crashes or embarrassing false detections
- [ ] The cost comparison ($684 vs. $10K+) is visible and credible in the showcase page
- [ ] The Expanso narrative is clear: "Expanso is what makes this work at the edge — it handles the pipeline, the buffering, the routing"

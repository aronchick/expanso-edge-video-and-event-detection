"""Edge-ISR sensor: YOLO + Gemini cascade per HACKATHON_SCRIPT.md §8.

One process per camera per Jetson. Reads RTSP, runs local YOLO detection,
optionally calls Gemini Flash for richer description, signs the event,
writes locally first, then pushes upstream with offline replay tolerance.
"""

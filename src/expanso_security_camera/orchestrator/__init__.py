"""Edge-ISR orchestrator: event sink, cross-sector correlator, dashboard server.

Per HACKATHON_SCRIPT.md §9. Runs on the laptop. Receives events from one or
more sensor processes, persists to SQLite, evaluates cross-sector correlation,
and broadcasts to the dashboard over WebSocket.
"""

"""Physics-informed beehive telemetry simulator.

Emits exactly the frames a real ESP32-S3 sentinel node emits, so the ingest
service, the models and the dashboards cannot tell the difference. When real
hardware arrives, nothing downstream changes.
"""

__version__ = "0.1.0"

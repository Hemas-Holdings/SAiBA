"""Secure desktop-to-backend streaming client. Implemented in Sprint 3.

Sends session control (start/pause/resume/stop) and mixed PCM only while
the session is running. Pause cuts the stream. Audio bytes are not logged
or written to disk.
"""

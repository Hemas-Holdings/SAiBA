"""UI-side placeholder for trigger-phrase hits.

The only matcher is the backend `stt.phrase_detector` module. This package
must not implement a second, more permissive detector. A later sprint may
mirror a backend hit here for the tray. No speaker identity. See
docs/session-model.md.
"""

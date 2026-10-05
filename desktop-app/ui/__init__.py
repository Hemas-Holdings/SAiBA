"""Tray icon and local Start / Pause / Resume / Stop.

These actions must not be invocable over the network. Pause is a capture
boundary: nothing is streamed or transcribed until Resume. See
docs/session-model.md.
"""

"""Answer playback (Sprint 5).

`client.PlaybackClient` receives answer audio on the separate, receive-only
playback socket; `player.AudioPlayer` plays it locally and reports when it
is speaking so capture can be muted. Audio is never written to disk.
"""

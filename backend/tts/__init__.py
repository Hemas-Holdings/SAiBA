"""Azure Speech streaming TTS (Sprint 5).

`speech.TextToSpeech` turns answer text that is already on the session
transcript into canonical PCM for the playback socket. Azure never plays
audio on the server. Does not persist audio or log answer text.
"""

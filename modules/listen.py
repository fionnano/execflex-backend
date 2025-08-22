import os
import tempfile
import sounddevice as sd
import numpy as np
import wave
from faster_whisper import WhisperModel

# Load the faster-whisper model (can also try "medium", "large-v2")
model = WhisperModel("base", device="cpu", compute_type="int8")

def record_audio(filename, duration=3, fs=44100):
    print("🎙️ Listening...")
    audio = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
    sd.wait()
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(audio.tobytes())

def listen():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmpfile:
        record_audio(tmpfile.name)
        print("🔍 Transcribing...")
        segments, _ = model.transcribe(tmpfile.name)
        return " ".join([segment.text for segment in segments])

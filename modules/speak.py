# modules/speak.py

import os
from elevenlabs.client import ElevenLabs
from playsound import playsound

# Set your API key and voice ID
ELEVEN_API_KEY = "sk_5718b0ecc020bd0212746aaf97dab1f9cb7605e3b1b468d1"
VOICE_ID = "g3kWXuhrNafLZGm1GXju"

# Create ElevenLabs client
client = ElevenLabs(api_key=ELEVEN_API_KEY)

def speak(text):
    if not text.strip():
        return

    print("🔊 Speaking via ElevenLabs (v1.x streaming)...")

    try:
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            model_id="eleven_monolingual_v1",
            text=text,
            output_format="mp3_44100"
        )

        temp_file = "temp_ai_dan_output.mp3"
        with open(temp_file, "wb") as f:
            for chunk in audio_stream:
                f.write(chunk)

        playsound(temp_file)
        os.remove(temp_file)

    except Exception as e:
        print(f"❌ Error during speech synthesis: {e}")

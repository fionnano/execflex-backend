import speech_recognition as sr

def listen():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.pause_threshold = 1.0
        r.energy_threshold = 300
        print("Listening...")
        audio = r.listen(source, timeout=None)

    try:
        text = r.recognize_google(audio)
        return text
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        return f"Speech Recognition error: {e}"

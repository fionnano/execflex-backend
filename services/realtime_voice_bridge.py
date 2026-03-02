"""
Realtime Voice Bridge - Orchestrates Twilio Media Streams, OpenAI Realtime, and ElevenLabs TTS.
Enables low-latency streaming voice conversations for outbound qualification calls.
"""
import asyncio
import base64
import json
import os
import struct
import time
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from config.app_config import OPENAI_API_KEY, ELEVEN_API_KEY, ELEVEN_VOICE_ID
from services.realtime_session_state import (
    get_session_manager,
    RealtimeSessionState,
    CallPhase
)
from services.voice_metrics import get_metrics_service


# Audio format constants
TWILIO_SAMPLE_RATE = 8000  # 8kHz mulaw from Twilio
OPENAI_SAMPLE_RATE = 24000  # 24kHz PCM for OpenAI
ELEVENLABS_SAMPLE_RATE = 24000  # 24kHz PCM from ElevenLabs

# OpenAI Realtime API endpoint
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"

# ElevenLabs WebSocket TTS endpoint
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_turbo_v2_5"


def mulaw_to_pcm16(mulaw_data: bytes) -> bytes:
    """Convert mulaw (8kHz) to PCM16 samples."""
    # Mulaw decoding table
    MULAW_DECODE = []
    for i in range(256):
        sign = -1 if (i & 0x80) else 1
        exponent = (i >> 4) & 0x07
        mantissa = i & 0x0F
        sample = sign * ((mantissa << (exponent + 3)) + (1 << (exponent + 3)) - 132)
        MULAW_DECODE.append(max(-32768, min(32767, sample)))

    pcm_samples = [MULAW_DECODE[b] for b in mulaw_data]
    return struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)


def pcm16_to_mulaw(pcm_data: bytes) -> bytes:
    """Convert PCM16 samples to mulaw."""
    # Simplified mulaw encoding
    MULAW_MAX = 32635
    MULAW_BIAS = 132

    samples = struct.unpack(f"<{len(pcm_data)//2}h", pcm_data)
    mulaw_bytes = []

    for sample in samples:
        sign = 0x80 if sample < 0 else 0
        sample = min(abs(sample), MULAW_MAX)
        sample = sample + MULAW_BIAS

        exponent = 7
        for exp in range(8):
            if sample < (1 << (exp + 8)):
                exponent = exp
                break

        mantissa = (sample >> (exponent + 3)) & 0x0F
        mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        mulaw_bytes.append(mulaw_byte)

    return bytes(mulaw_bytes)


def resample_8k_to_24k(pcm_8k: bytes) -> bytes:
    """Resample 8kHz PCM to 24kHz PCM (3x upsampling with linear interpolation)."""
    samples = struct.unpack(f"<{len(pcm_8k)//2}h", pcm_8k)
    resampled = []

    for i in range(len(samples) - 1):
        s1, s2 = samples[i], samples[i + 1]
        resampled.append(s1)
        resampled.append(int(s1 + (s2 - s1) / 3))
        resampled.append(int(s1 + 2 * (s2 - s1) / 3))

    if samples:
        resampled.append(samples[-1])

    return struct.pack(f"<{len(resampled)}h", *resampled)


def resample_24k_to_8k(pcm_24k: bytes) -> bytes:
    """Resample 24kHz PCM to 8kHz PCM (3x downsampling)."""
    samples = struct.unpack(f"<{len(pcm_24k)//2}h", pcm_24k)
    resampled = [samples[i] for i in range(0, len(samples), 3)]
    return struct.pack(f"<{len(resampled)}h", *resampled)


@dataclass
class BridgeConfig:
    """Configuration for the voice bridge."""
    call_sid: str
    job_id: Optional[str] = None
    interaction_id: Optional[str] = None
    user_id: Optional[str] = None
    signup_mode: Optional[str] = None
    max_duration_seconds: int = 600  # 10 minutes
    system_prompt: Optional[str] = None


class RealtimeVoiceBridge:
    """
    Manages the real-time voice bridge between Twilio, OpenAI, and ElevenLabs.
    """

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.call_sid = config.call_sid
        self.session_manager = get_session_manager()
        self.metrics_service = get_metrics_service()

        # WebSocket connections
        self.twilio_ws: Optional[websockets.WebSocketServerProtocol] = None
        self.openai_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.elevenlabs_ws: Optional[websockets.WebSocketClientProtocol] = None

        # State
        self.session: Optional[RealtimeSessionState] = None
        self.stream_sid: Optional[str] = None
        self.is_running = False
        self.pending_audio_chunks: list = []

        # Callbacks
        self.on_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None
        self.on_response_start: Optional[Callable[[], Awaitable[None]]] = None
        self.on_response_end: Optional[Callable[[str], Awaitable[None]]] = None

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for OpenAI Realtime."""
        base_prompt = self.config.system_prompt or self._default_system_prompt()

        # Add session context if available
        if self.session:
            context = self.session.get_system_context()
            return f"{base_prompt}\n\nCurrent session context:\n{context}"

        return base_prompt

    def _default_system_prompt(self) -> str:
        """Default system prompt for qualification calls."""
        signup_mode = self.config.signup_mode or "unknown"

        if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
            mode_context = "The user is an executive looking for job opportunities."
        elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
            mode_context = "The user is looking to hire executive talent for their organization."
        else:
            mode_context = "Determine whether the user is looking to hire executives or is an executive seeking opportunities."

        return f"""You are Ai-dan, a friendly voice assistant for ExecFlex, a platform connecting companies with executive talent.

{mode_context}

CONVERSATION STYLE:
- Be warm, professional, and concise
- Ask ONE question at a time
- Keep responses under 20 seconds when spoken (about 50-70 words max)
- Listen actively and acknowledge what the user says
- Don't repeat questions that have been answered

CONVERSATION GOALS:
1. Confirm their intent (hiring vs job seeking)
2. Understand their motivation (why ExecFlex, why now)
3. Learn about role preferences (titles, industries)
4. Understand location and availability preferences
5. Identify any constraints or deal-breakers

IMPORTANT RULES:
- Never ask for information already provided
- If the user wants to end the call, thank them politely and close
- After 8-10 minutes or when enough info is gathered, begin closing the conversation
- Be natural and conversational, not robotic
"""

    async def start(self, twilio_ws: websockets.WebSocketServerProtocol) -> None:
        """Start the bridge with a Twilio WebSocket connection."""
        self.twilio_ws = twilio_ws
        self.is_running = True

        # Initialize session
        self.session = self.session_manager.create_session(
            self.call_sid,
            job_id=self.config.job_id,
            interaction_id=self.config.interaction_id,
            user_id=self.config.user_id,
            signup_mode=self.config.signup_mode
        )

        # Start metrics
        self.metrics_service.start_call(
            self.call_sid,
            job_id=self.config.job_id,
            interaction_id=self.config.interaction_id
        )

        try:
            # Connect to OpenAI Realtime API
            await self._connect_openai()

            # Run the main loop
            await asyncio.gather(
                self._handle_twilio_messages(),
                self._handle_openai_messages(),
                self._check_duration_limit()
            )
        except Exception as e:
            print(f"Bridge error for {self.call_sid}: {e}")
            self.metrics_service.record_event(
                self.call_sid,
                "bridge_error",
                status="error",
                provider="system",
                metadata={"error": str(e)}
            )
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the bridge and clean up resources."""
        self.is_running = False

        # Close WebSocket connections
        if self.openai_ws:
            try:
                await self.openai_ws.close()
            except Exception:
                pass

        if self.elevenlabs_ws:
            try:
                await self.elevenlabs_ws.close()
            except Exception:
                pass

        # End session and persist metrics
        self.session_manager.end_session(self.call_sid)
        self.metrics_service.end_call(self.call_sid)

    async def _connect_openai(self) -> None:
        """Connect to OpenAI Realtime API."""
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }

        try:
            self.openai_ws = await websockets.connect(
                OPENAI_REALTIME_URL,
                additional_headers=headers
            )

            # Configure the session
            await self._configure_openai_session()

            self.metrics_service.record_event(
                self.call_sid,
                "openai_connected",
                provider="openai"
            )
        except Exception as e:
            print(f"Failed to connect to OpenAI: {e}")
            raise

    async def _configure_openai_session(self) -> None:
        """Configure the OpenAI Realtime session."""
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": self._get_system_prompt(),
                "voice": "alloy",  # OpenAI voice (we'll use ElevenLabs for TTS)
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "temperature": 0.8,
                "max_response_output_tokens": 200
            }
        }

        await self.openai_ws.send(json.dumps(session_config))

    async def _handle_twilio_messages(self) -> None:
        """Handle incoming messages from Twilio Media Streams."""
        try:
            async for message in self.twilio_ws:
                if not self.is_running:
                    break

                data = json.loads(message)
                event_type = data.get("event")

                if event_type == "connected":
                    print(f"Twilio stream connected for {self.call_sid}")

                elif event_type == "start":
                    self.stream_sid = data.get("streamSid")
                    self.session.phase = CallPhase.GREETING
                    print(f"Twilio stream started: {self.stream_sid}")

                    # Send initial greeting
                    await self._send_greeting()

                elif event_type == "media":
                    # Forward audio to OpenAI
                    await self._forward_audio_to_openai(data["media"]["payload"])

                elif event_type == "stop":
                    print(f"Twilio stream stopped for {self.call_sid}")
                    self.is_running = False

        except ConnectionClosed:
            print(f"Twilio connection closed for {self.call_sid}")
        except Exception as e:
            print(f"Error handling Twilio messages: {e}")

    async def _handle_openai_messages(self) -> None:
        """Handle incoming messages from OpenAI Realtime API."""
        if not self.openai_ws:
            return

        try:
            async for message in self.openai_ws:
                if not self.is_running:
                    break

                data = json.loads(message)
                event_type = data.get("type")

                # Handle different event types
                if event_type == "session.created":
                    print(f"OpenAI session created for {self.call_sid}")

                elif event_type == "input_audio_buffer.speech_started":
                    # User started speaking
                    if self.session:
                        self.session.pending_user_audio = True

                elif event_type == "input_audio_buffer.speech_stopped":
                    # User stopped speaking
                    self.metrics_service.record_user_speech_end(self.call_sid)
                    if self.session:
                        self.session.pending_user_audio = False

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # Got final transcript
                    transcript = data.get("transcript", "")
                    if transcript and self.session:
                        self.session.last_user_text = transcript
                        self.session.add_turn("user", transcript)
                        print(f"User said: {transcript}")

                elif event_type == "response.audio_transcript.delta":
                    # Streaming response text
                    delta = data.get("delta", "")
                    # Could stream to ElevenLabs here for lower latency

                elif event_type == "response.audio_transcript.done":
                    # Complete response text
                    transcript = data.get("transcript", "")
                    if transcript and self.session:
                        self.session.add_turn("assistant", transcript)

                elif event_type == "response.audio.delta":
                    # Streaming audio from OpenAI (if not using ElevenLabs)
                    audio_b64 = data.get("delta", "")
                    if audio_b64:
                        await self._send_audio_to_twilio(audio_b64)
                        # Record first audio timing
                        self.metrics_service.record_first_audio(self.call_sid)

                elif event_type == "response.audio.done":
                    # Response audio complete
                    self.metrics_service.record_response_complete(self.call_sid)
                    if self.session:
                        self.session.is_assistant_speaking = False
                        self.session.clear_errors()

                elif event_type == "response.done":
                    # Full response complete
                    response = data.get("response", {})
                    status = response.get("status")
                    if status == "completed":
                        if self.session:
                            self.session.turn_count += 1

                elif event_type == "error":
                    error = data.get("error", {})
                    print(f"OpenAI error: {error}")
                    self.metrics_service.record_event(
                        self.call_sid,
                        "openai_error",
                        status="error",
                        provider="openai",
                        metadata=error
                    )
                    # Handle retry logic
                    if self.session and not self.session.record_error():
                        # Max retries exceeded - end call politely
                        await self._send_polite_close("technical_error")

        except ConnectionClosed:
            print(f"OpenAI connection closed for {self.call_sid}")
        except Exception as e:
            print(f"Error handling OpenAI messages: {e}")

    async def _forward_audio_to_openai(self, audio_b64: str) -> None:
        """Forward audio from Twilio to OpenAI."""
        if not self.openai_ws:
            return

        try:
            # Decode mulaw audio
            mulaw_audio = base64.b64decode(audio_b64)

            # Convert mulaw 8kHz to PCM16 24kHz
            pcm_8k = mulaw_to_pcm16(mulaw_audio)
            pcm_24k = resample_8k_to_24k(pcm_8k)

            # Send to OpenAI
            audio_event = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_24k).decode("utf-8")
            }
            await self.openai_ws.send(json.dumps(audio_event))

        except Exception as e:
            print(f"Error forwarding audio: {e}")

    async def _send_audio_to_twilio(self, audio_b64: str) -> None:
        """Send audio from OpenAI/ElevenLabs to Twilio."""
        if not self.twilio_ws or not self.stream_sid:
            return

        try:
            # Decode PCM16 24kHz audio
            pcm_24k = base64.b64decode(audio_b64)

            # Convert to mulaw 8kHz for Twilio
            pcm_8k = resample_24k_to_8k(pcm_24k)
            mulaw_audio = pcm16_to_mulaw(pcm_8k)

            # Send to Twilio
            media_event = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": base64.b64encode(mulaw_audio).decode("utf-8")
                }
            }
            await self.twilio_ws.send(json.dumps(media_event))

        except Exception as e:
            print(f"Error sending audio to Twilio: {e}")

    async def _send_greeting(self) -> None:
        """Send initial greeting when call starts."""
        signup_mode = self.config.signup_mode or "unknown"

        if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
            greeting = (
                "Hi, this is Ai-dan from ExecFlex. I noticed you just logged in "
                "looking for executive opportunities. Have I caught you at a bad time?"
            )
        elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
            greeting = (
                "Hello, this is Ai-dan from ExecFlex. I noticed you just logged in "
                "looking for executive talent for your organization. Have I caught you at a bad time?"
            )
        else:
            greeting = (
                "Hello, this is Ai-dan from ExecFlex. I noticed you just logged in. "
                "Are you looking to hire executive talent, or are you an executive "
                "looking for opportunities?"
            )

        # Send text to OpenAI to generate response audio
        create_response = {
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "instructions": f"Say exactly this greeting, naturally and warmly: '{greeting}'"
            }
        }

        if self.openai_ws:
            await self.openai_ws.send(json.dumps(create_response))
            if self.session:
                self.session.add_turn("assistant", greeting)
                self.session.phase = CallPhase.DISCOVERY
                self.session.is_assistant_speaking = True

        self.metrics_service.start_turn(self.call_sid, 1)

    async def _send_polite_close(self, reason: str) -> None:
        """Send polite closing message and end call."""
        close_messages = {
            "technical_error": "I apologize, but I'm having some technical difficulties. Let's try again another time. Thank you for your patience.",
            "max_duration": "We've been chatting for a while now. Thank you so much for your time today. We'll be in touch with any opportunities that match your profile. Goodbye!",
            "user_request": "No problem at all. Thank you for your time. We'll follow up if we find good matches for you. Goodbye!"
        }

        message = close_messages.get(reason, close_messages["technical_error"])

        if self.openai_ws:
            create_response = {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "instructions": f"Say this closing message naturally: '{message}'"
                }
            }
            await self.openai_ws.send(json.dumps(create_response))

        if self.session:
            self.session.phase = CallPhase.CLOSING
            self.session.add_turn("assistant", message)

        self.metrics_service.record_event(
            self.call_sid,
            "call_closing",
            provider="system",
            metadata={"reason": reason}
        )

        # Give time for message to play, then stop
        await asyncio.sleep(10)
        self.is_running = False

    async def _check_duration_limit(self) -> None:
        """Periodically check if call has exceeded max duration."""
        while self.is_running:
            await asyncio.sleep(30)  # Check every 30 seconds

            if self.session and self.session.is_expired():
                print(f"Call {self.call_sid} exceeded max duration")
                await self._send_polite_close("max_duration")
                break


async def handle_media_stream(
    websocket: websockets.WebSocketServerProtocol,
    path: str,
    config: BridgeConfig
) -> None:
    """
    Entry point for handling a Twilio Media Stream WebSocket connection.
    Called from the Flask route that upgrades to WebSocket.
    """
    bridge = RealtimeVoiceBridge(config)
    await bridge.start(websocket)

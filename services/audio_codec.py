"""
Audio codec helpers — mulaw <-> PCM16 conversion.

Used by the Twilio Media Streams bridge to convert between:
  - mulaw 8kHz (Twilio phone-line format)
  - PCM16 samples (OpenAI Realtime API format)

Pure-Python implementations — no audioop, no external deps beyond
`struct` from the standard library. Keeps this module tiny and
trivially testable.
"""
import struct


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

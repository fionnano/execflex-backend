"""Text-encoding helpers — keep UTF-8 correct end to end.

The recurring bug this fixes: spreadsheets (Excel, Numbers) export CSV in the
operating system's ANSI code page — Windows-1252 / Latin-1 — not UTF-8. An
Irish name like "Seán" is then the byte sequence ``…65 E1 6E…``. Decoding those
bytes as UTF-8 with ``errors="replace"`` turns the invalid ``0xE1`` into the
Unicode replacement character, so the name is stored as "Se�n" and shows up
garbled everywhere it is later displayed (candidate records, screening
transcripts). Once replaced, the original byte is gone — so the fix must be at
decode time: try UTF-8 first, then fall back to Windows-1252/Latin-1 instead of
destroying the bytes.
"""

from __future__ import annotations


def decode_text_bytes(raw: bytes) -> str:
    """Decode uploaded text bytes to ``str`` without garbling accents.

    Order: UTF-8 (with optional BOM) → Windows-1252 → Latin-1. Latin-1 maps all
    256 byte values, so it never raises and is the guaranteed final fallback —
    the previous ``errors="replace"`` path (which produced "Se�n") is gone.
    """
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Unreachable in practice (latin-1 accepts any byte), kept as a hard guard.
    return raw.decode("utf-8", errors="replace")

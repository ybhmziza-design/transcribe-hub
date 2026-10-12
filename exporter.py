"""
Exporter module to convert transcription segment data into various formats:
- Plain Text (.txt)
- SubRip (.srt)
- WebVTT (.vtt)
- JSON (.json)
"""
import json

def seconds_to_srt_time(seconds: float) -> str:
    """Format seconds (float) into SRT format: HH:MM:SS,mmm"""
    if seconds is None:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def seconds_to_vtt_time(seconds: float) -> str:
    """Format seconds (float) into WebVTT format: HH:MM:SS.mmm"""
    if seconds is None:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"

def seconds_to_simple_timestamp(seconds: float) -> str:
    """Format seconds into readable timestamp: [MM:SS] or [HH:MM:SS]"""
    if seconds is None:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"[{hrs:02d}:{mins:02d}:{secs:02d}]"
    return f"[{mins:02d}:{secs:02d}]"

def parse_segments(content_json: str):
    """Safely parse JSON segment array."""
    if not content_json:
        return []
    try:
        data = json.loads(content_json)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def generate_srt(content_json: str, fallback_text: str = '') -> str:
    """Generate SubRip Subtitle file (.srt)."""
    segments = parse_segments(content_json)
    if not segments and fallback_text:
        # Fallback to single block if no segments
        return f"1\n00:00:00,000 --> 00:00:10,000\n{fallback_text.strip()}\n"

    output = []
    idx = 1
    for seg in segments:
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        start = float(seg.get('start', 0.0))
        end = float(seg.get('end', start + 3.0))
        if end <= start:
            end = start + 2.5
        speaker = seg.get('speaker', '').strip()
        line_content = f"{speaker}: {text}" if speaker else text

        srt_chunk = (
            f"{idx}\n"
            f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n"
            f"{line_content}\n"
        )
        output.append(srt_chunk)
        idx += 1

    return "\n".join(output)

def generate_vtt(content_json: str, fallback_text: str = '') -> str:
    """Generate WebVTT file (.vtt)."""
    segments = parse_segments(content_json)
    output = ["WEBVTT\n"]

    if not segments and fallback_text:
        output.append(f"00:00:00.000 --> 00:00:10.000\n{fallback_text.strip()}\n")
        return "\n".join(output)

    idx = 1
    for seg in segments:
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        start = float(seg.get('start', 0.0))
        end = float(seg.get('end', start + 3.0))
        if end <= start:
            end = start + 2.5
        speaker = seg.get('speaker', '').strip()
        
        if speaker:
            line_content = f"<v {speaker}>{text}</v>"
        else:
            line_content = text

        vtt_chunk = (
            f"{idx}\n"
            f"{seconds_to_vtt_time(start)} --> {seconds_to_vtt_time(end)}\n"
            f"{line_content}\n"
        )
        output.append(vtt_chunk)
        idx += 1

    return "\n".join(output)

def generate_txt(content_json: str, fallback_text: str = '') -> str:
    """Generate Plain Text with Speaker and Timestamp labels."""
    segments = parse_segments(content_json)
    if not segments:
        return fallback_text.strip() if fallback_text else "No transcription content available."

    lines = []
    for seg in segments:
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        start = float(seg.get('start', 0.0))
        ts = seconds_to_simple_timestamp(start)
        speaker = (seg.get('speaker') or '').strip()
        if speaker:
            lines.append(f"{ts} {speaker}: {text}")
        else:
            lines.append(f"{ts} {text}")

    return "\n\n".join(lines)

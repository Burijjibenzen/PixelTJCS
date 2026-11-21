"""气泡分段，顺序播放的台词按长度分配阅读时间。"""
from backend.config import SOCIAL


def dialogue_segments(text):
    parts, part = [], ''
    for character in text:
        part += character
        if len(part) >= SOCIAL.bubble_segment_chars or (len(part) >= 30 and character in '。！？；\n'):
            if part.strip():
                parts.append(part.strip())
            part = ''
    if part.strip():
        parts.append(part.strip())
    return [dict(text=part, seconds=min(SOCIAL.bubble_seconds,4.0+len(part)/20)) for part in parts]

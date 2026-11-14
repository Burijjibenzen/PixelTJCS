"""原版气泡分段：新台词即时显示，每段最多停留配置中的现实秒数。"""
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
    return [dict(text=part, seconds=SOCIAL.bubble_seconds) for part in parts]

"""活动来源对应的调度档位。"""

TIERS = ('low', 'medium', 'high')
HIGH_SOURCES = {'whisper', 'manual'}
MEDIUM_SOURCES = {'event', 'memory', 'event_followup'}


def activity_tier(source):
    if source in HIGH_SOURCES:
        return 'high'
    return 'medium' if source in MEDIUM_SOURCES else 'low'

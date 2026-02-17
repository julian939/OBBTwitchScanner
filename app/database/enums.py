from enum import Enum


class PointReason(str, Enum):
    STREAM_TIME = "stream_time"
    DAILY_BONUS = "daily_bonus"
    STREAK_BONUS = "streak_bonus" # not sure about that yet
    MANUAL = "manual"
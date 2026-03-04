from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Twitch
    twitch_client_id: str
    twitch_client_secret: str

    reconciliation_interval_minutes: int = 5
    tracked_categories: list[str] = ["Oh Baby! Kart", "Shogun Curse", "Bam Bam Boom", "Pawker"]

    # Points
    points_per_minute: int = 1
    daily_bonus_points: int = 50
    streak_bonus_multiplier: int = 5
    event_multiplier: int = 2
    live_points_interval_minutes: int = 5

    # Webhook
    webhook_secret: str
    webhook_callback_url: str

    # Admin
    admin_secret: str

    # Discord
    discord_bot_token: str = ""
    discord_guild_id: int = 0
    discord_admin_role_ids: list[int] = []
    discord_admin_user_ids: list[int] = []
    discord_registration_channel_id: int = 0
    discord_live_role_id: int = 0
    discord_top1_role_id: int = 0
    discord_top5_role_id: int = 0
    discord_game_channels: dict[str, int] = {}
    discord_footer_tips: list[str] = [
        "Use /register to become a tracked streamer.",
        "Use /live to see who's currently streaming.",
        "Use /streamer <name> for detailed stats.",
        "Use /info to learn how points work.",
        "Stream daily to build your streak bonus!",
        "First stream of the day earns a daily bonus!",
    ]

    # Database
    database_url: str

    # Notification cooldowns
    notify_offline_delay_minutes: int = 5
    notify_offline_min_duration_minutes: int = 5

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
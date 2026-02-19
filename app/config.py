from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Twitch
    twitch_client_id: str
    twitch_client_secret: str

    reconciliation_interval_minutes: int = 5
    tracked_categories: list[str] = ["Oh Baby! Kart", "Shogun Curse", "Bam Bam Boom", "Pawker"]

    # Webhook
    webhook_secret: str
    webhook_callback_url: str

    # Admin
    admin_secret: str

    # Discord
    discord_bot_token: str = ""
    discord_webhook_url: str = ""
    discord_notification_channel_id: int = 0
    discord_guild_id: int = 0
    discord_admin_role_ids: list[int] = []

    # Database
    database_url: str

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
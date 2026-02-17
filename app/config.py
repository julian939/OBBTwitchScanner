from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Twitch
    twitch_client_id: str
    twitch_client_secret: str
    reconciliation_interval_minutes: int = 15

    # Webhook
    webhook_secret: str
    webhook_callback_url: str

    # Admin
    admin_secret: str

    # Discord
    discord_bot_token: str = ""
    discord_webhook_url: str = ""
    discord_notification_channel_id: int = 0
    discord_guild_id: int = 1259284165975871620
    discord_admin_role_id: int = 1365413035069673644

    # Database
    database_url: str

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
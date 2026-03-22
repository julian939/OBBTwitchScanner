from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.database.database import Base


class Streamer(Base):
    __tablename__ = "streamers"

    id = Column(String, primary_key=True)
    login = Column(String, unique=True, index=True)
    display_name = Column(String)
    profile_image_url = Column(String, nullable=True)
    discord_id = Column(String, nullable=True, unique=True)
    is_live = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False, server_default="0")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    streams = relationship("Stream", back_populates="streamer")
    subscriptions = relationship("Subscription", back_populates="streamer")
    point_transactions = relationship("PointTransaction", back_populates="streamer")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    streamer_id = Column(String, ForeignKey("streamers.id"), index=True)
    game_name = Column(String, nullable=True)
    started_at = Column(DateTime)
    ended_at = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    last_points_at = Column(DateTime, nullable=True)

    streamer = relationship("Streamer", back_populates="streams")
    point_transactions = relationship("PointTransaction", back_populates="stream")


class PointTransaction(Base):
    __tablename__ = "point_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    streamer_id = Column(String, ForeignKey("streamers.id"), index=True)
    points = Column(Integer)
    reason = Column(String)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    streamer = relationship("Streamer", back_populates="point_transactions")
    stream = relationship("Stream", back_populates="point_transactions")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("streamers.id"), index=True)
    type = Column(String)
    status = Column(String, default="enabled")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    streamer = relationship("Streamer", back_populates="subscriptions")


class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_id = Column(String, index=True)
    discord_username = Column(String)
    twitch_username = Column(String)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, nullable=True)


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    id = Column(String, primary_key=True)
    processed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
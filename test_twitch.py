import os
import sys
import logging
from app.config import get_settings
from app.integrations.twitch import twitch_api
from app.database.database import init_db, SessionLocal
from app.database.models import Streamer

logging.basicConfig(level=logging.DEBUG)

def test():
    init_db()
    db = SessionLocal()
    streamers = db.query(Streamer).all()
    if not streamers:
        print("No streamers in db, testing with dummy id")
        streamer_id = "12345678"
    else:
        streamer_id = streamers[0].id
        
    print(f"Testing for streamer_id: {streamer_id}")
    try:
        res = twitch_api.create_eventsub_subscription("stream.online", streamer_id)
        print("Success:", res)
    except Exception as e:
        print("Failed:", e)

if __name__ == "__main__":
    test()

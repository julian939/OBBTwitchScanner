# OBB Twitch Scanner

FastAPI-Backend plus Discord-Bot für Twitch-Tracking, Rollen, Punkte und Benachrichtigungen.

## Ziel-Setup

Das Projekt ist jetzt für einen schlanken Betrieb auf einer VM ohne Docker vorbereitet:

- `git` zum Auschecken und Aktualisieren
- Python-`venv` für die Abhängigkeiten
- `uvicorn` als App-Server
- `systemd` für den dauerhaften Dienst

## Lokales Setup

1. Python 3.12 oder neuer installieren.
2. Repository klonen.
3. Virtuelle Umgebung anlegen und aktivieren:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

5. Umgebung konfigurieren:

```bash
cp .env.example .env
```

6. `DATABASE_URL` setzen.

Für eine kleine Oracle-Cloud-VM ist SQLite am sparsamsten:

```env
DATABASE_URL=sqlite:///./data/stream_tracker.db
BACKUP_STORAGE_PATH=./data
```

Wenn du lieber PostgreSQL nutzt, kannst du hier natürlich einen `postgresql://...`-Wert setzen.

7. App starten:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## systemd

Eine passende Unit liegt unter [`deploy/systemd/obb-twitchscanner.service`](./deploy/systemd/obb-twitchscanner.service).

Typischer Ablauf auf der VM:

```bash
sudo cp deploy/systemd/obb-twitchscanner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now obb-twitchscanner
```

Passe davor im Unit-File diese Werte an:

- `WorkingDirectory`
- `EnvironmentFile`
- `User` und `Group`
- den Pfad zur virtuellen Umgebung

## Entfernte Altlasten

Die alten Railway-/Docker-Artefakte wurden entfernt:

- `Dockerfile`
- `Procfile`
- `run.py`

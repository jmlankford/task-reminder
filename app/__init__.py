import logging
import os
from flask import Flask, render_template
from .models import db, Config

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    db_path = os.environ.get("DATABASE_PATH", "/data/taskreminder.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _run_migrations()
        _seed_config()

    from .routes.reminders import reminders_bp
    from .routes.config import config_bp
    from .routes.telegram import telegram_bp
    from .routes.gcal import gcal_bp
    from .routes.receipt import receipt_bp
    from .routes.ha import ha_bp

    app.register_blueprint(reminders_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(telegram_bp)
    app.register_blueprint(gcal_bp)
    app.register_blueprint(receipt_bp)
    app.register_blueprint(ha_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    # Start background services (guard against double-start with Werkzeug reloader)
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from .scheduler import init_scheduler
        init_scheduler(app)

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token:
            from .telegram_bot import start_bot_thread
            start_bot_thread(app, token)
        else:
            logger.info("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled.")

    return app


def _run_migrations():
    """Apply schema changes that db.create_all() won't handle on existing databases."""
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col, definition in [
            ("notes_details",    "TEXT"),
            ("last_notified_at", "DATETIME"),
            ("gcal_event_id",    "VARCHAR(255)"),
            ("remind_at",        "DATETIME"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE reminders ADD COLUMN {col} {definition}"))
                conn.commit()
            except Exception:
                pass  # Column already exists — safe to ignore


def _seed_config():
    if not Config.query.get("max_active"):
        db.session.add(Config(key="max_active", value="5"))
        db.session.commit()

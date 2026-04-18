"""Maintenance tasks — periodic cleanup, icon refresh, integrity checks.

These run on the Celery beat schedule or can be triggered via the admin API.
"""

import logging
import os

from reddarr.tasks import app
from reddarr.database import SessionLocal, init_engine
from reddarr.models import Target, Media, Post

logger = logging.getLogger(__name__)


@app.task(name="reddarr.tasks.maintenance.refresh_target_icons")
def refresh_target_icons():
    """Fetch current icons for all targets from Reddit.

    Replaces the old _refresh_target_icons() in web/app.py startup.
    """
    import requests
    from reddarr.config import get_settings

    init_engine()
    settings = get_settings()
    session = requests.Session()
    session.headers["User-Agent"] = settings.reddit_user_agent

    with SessionLocal() as db:
        targets = db.query(Target).filter(Target.enabled.is_(True)).all()
        updated = 0

        for target in targets:
            try:
                if target.type == "subreddit":
                    url = f"https://www.reddit.com/r/{target.name}/about.json"
                else:
                    url = f"https://www.reddit.com/user/{target.name}/about.json"

                r = session.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    icon = (
                        data.get("community_icon", "").split("?")[0]
                        or data.get("icon_img", "").split("?")[0]
                        or data.get("snoovatar_img", "")
                    )
                    if icon and icon != target.icon_url:
                        target.icon_url = icon
                        updated += 1
            except Exception as e:
                logger.warning(f"Icon refresh failed for {target.name}: {e}")

        db.commit()
        logger.info(f"Refreshed {updated} target icons")
        return {"updated": updated}


@app.task(name="reddarr.tasks.maintenance.cleanup_failed_downloads")
def cleanup_failed_downloads(max_retries: int = 10):
    """Mark permanently-failed downloads so they stop being retried."""
    init_engine()

    with SessionLocal() as db:
        count = (
            db.query(Media)
            .filter(Media.status == "failed", Media.retries >= max_retries)
            .update({"status": "abandoned"})
        )
        db.commit()
        logger.info(f"Marked {count} downloads as abandoned (>{max_retries} retries)")
        return {"abandoned": count}


@app.task(name="reddarr.tasks.maintenance.integrity_check")
def integrity_check():
    """Verify media files on disk match database records.

    Replaces scripts/integrity_check.py.
    """
    init_engine()

    with SessionLocal() as db:
        media_items = (
            db.query(Media)
            .filter(Media.status == "done", Media.file_path.isnot(None))
            .all()
        )

        missing = 0
        total = len(media_items)

        for m in media_items:
            if not os.path.exists(m.file_path):
                m.status = "missing"
                missing += 1

        db.commit()
        logger.info(f"Integrity check: {missing}/{total} files missing")
        return {"total": total, "missing": missing, "ok": total - missing}


@app.task(name="reddarr.tasks.maintenance.purge_orphan_thumbnails")
def purge_orphan_thumbnails():
    """Remove thumbnail files that no longer have a corresponding media record."""
    from reddarr.config import get_settings

    init_engine()
    settings = get_settings()

    with SessionLocal() as db:
        known_thumbs = set(
            row[0]
            for row in db.query(Media.thumb_path)
            .filter(Media.thumb_path.isnot(None))
            .all()
        )

    removed = 0
    thumb_dir = settings.thumb_path
    if os.path.isdir(thumb_dir):
        for root, dirs, files in os.walk(thumb_dir):
            for f in files:
                full = os.path.join(root, f)
                if full not in known_thumbs:
                    try:
                        os.remove(full)
                        removed += 1
                    except OSError:
                        pass

    logger.info(f"Purged {removed} orphan thumbnails")
    return {"removed": removed}

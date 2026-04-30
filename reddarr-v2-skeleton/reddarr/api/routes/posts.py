"""Posts API routes - listing, detail, search, hide/unhide, delete.

Extracts the post-related endpoints from the old web/app.py monolith:
  /api/posts          -> list_posts()
  /api/post/{id}      -> get_post()
  /api/post/{id}/history -> get_post_history()
  /api/search         -> search_posts()
  /api/post/{id}/hide -> hide_post()
  /api/post/{id}/unhide -> unhide_post()
  /api/post/{id}/delete -> delete_post()
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func, text, desc, or_
from sqlalchemy.orm import Session

from reddarr.database import get_db
from reddarr.models import Post, Comment, Media, PostHistory, CommentHistory
from reddarr.api.auth import require_api_key
from reddarr.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["posts"])


@router.get("/posts")
def list_posts(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
    sort: str = Query("newest", regex="^(newest|oldest)$"),
    show_hidden: bool = False,
    db: Session = Depends(get_db),
):
    """List posts with pagination and filtering.

    This replaces the 300-line /api/posts endpoint from web/app.py,
    using SQLAlchemy ORM queries instead of raw SQL.
    """
    query = db.query(Post)

    if not show_hidden:
        query = query.filter(Post.hidden.is_(False))

    if subreddit:
        query = query.filter(func.lower(Post.subreddit) == subreddit.lower())

    if author:
        query = query.filter(func.lower(Post.author) == author.lower())

    # Sort
    if sort == "newest":
        query = query.order_by(desc(Post.created_utc))
    else:
        query = query.order_by(Post.created_utc)

    # Paginate
    total = query.count()
    posts = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "posts": [_serialize_post(p, db) for p in posts],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/post/{post_id}")
def get_post(post_id: str, db: Session = Depends(get_db)):
    """Get a single post with its media and comments."""
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    result = _serialize_post(post, db, include_comments=True)
    return result


@router.get("/post/{post_id}/history")
def get_post_history(post_id: str, db: Session = Depends(get_db)):
    """Get version history for a post."""
    versions = (
        db.query(PostHistory)
        .filter_by(post_id=post_id)
        .order_by(desc(PostHistory.version))
        .all()
    )

    return {
        "post_id": post_id,
        "versions": [
            {
                "version": v.version,
                "title": v.title,
                "selftext": v.selftext,
                "author": v.author,
                "is_deleted": v.is_deleted,
                "captured_at": v.captured_at.isoformat() if v.captured_at else None,
            }
            for v in versions
        ],
    }


@router.get("/comment/{comment_id}/history")
def get_comment_history(comment_id: str, db: Session = Depends(get_db)):
    """Get version history for a comment."""
    versions = (
        db.query(CommentHistory)
        .filter_by(comment_id=comment_id)
        .order_by(desc(CommentHistory.version))
        .all()
    )

    return {
        "comment_id": comment_id,
        "versions": [
            {
                "version": v.version,
                "body": v.body,
                "author": v.author,
                "is_deleted": v.is_deleted,
                "captured_at": v.captured_at.isoformat() if v.captured_at else None,
            }
            for v in versions
        ],
    }


@router.get("/search")
def search_posts(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    subreddit: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Full-text search across posts.

    Uses PostgreSQL tsvector/tsquery for efficient FTS,
    replacing the raw SQL from the old search endpoint.
    """
    tsquery = func.plainto_tsquery("english", q)

    query = (
        db.query(Post)
        .filter(Post.tsv.op("@@")(tsquery))
        .filter(Post.hidden.is_(False))
    )

    if subreddit:
        query = query.filter(func.lower(Post.subreddit) == subreddit.lower())

    # Rank by relevance
    rank = func.ts_rank(Post.tsv, tsquery)
    query = query.order_by(desc(rank))

    total = query.count()
    posts = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "query": q,
        "posts": [_serialize_post(p, db) for p in posts],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/post/{post_id}/hide")
def hide_post(post_id: str, db: Session = Depends(get_db)):
    """Soft-hide a post (sets hidden=True)."""
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.hidden = True
    post.hidden_at = datetime.utcnow()
    db.commit()
    return {"status": "hidden", "post_id": post_id}


@router.post("/post/{post_id}/unhide")
def unhide_post(post_id: str, db: Session = Depends(get_db)):
    """Un-hide a previously hidden post."""
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.hidden = False
    post.hidden_at = None
    db.commit()
    return {"status": "visible", "post_id": post_id}


@router.post(
    "/post/{post_id}/delete",
    dependencies=[Depends(require_api_key)],
)
def delete_post(
    post_id: str,
    delete_media: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Hard-delete a post and optionally its media files.

    Requires API key authentication.
    """
    import os

    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Delete media files from disk if requested
    if delete_media:
        media_items = db.query(Media).filter_by(post_id=post_id).all()
        for m in media_items:
            for path in [m.file_path, m.thumb_path]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    # Delete DB records (cascade: media, comments, history)
    db.query(Media).filter_by(post_id=post_id).delete()
    db.query(Comment).filter_by(post_id=post_id).delete()
    db.query(PostHistory).filter_by(post_id=post_id).delete()
    db.query(CommentHistory).filter_by(post_id=post_id).delete()
    db.query(Post).filter_by(id=post_id).delete()
    db.commit()

    return {"status": "deleted", "post_id": post_id}


@router.get("/debug/{post_id}", dependencies=[Depends(require_api_key)])
def debug_post(post_id: str, db: Session = Depends(get_db)):
    """Debug endpoint - returns full raw data for a post."""
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    media_items = db.query(Media).filter_by(post_id=post_id).all()

    return {
        "post": {
            "id": post.id,
            "raw": post.raw,
            "url": post.url,
            "media_url": post.media_url,
        },
        "media": [
            {
                "id": m.id,
                "url": m.url,
                "file_path": m.file_path,
                "status": m.status,
                "error": m.error_message,
            }
            for m in media_items
        ],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_post(post: Post, db: Session, include_comments: bool = False) -> dict:
    """Serialize a Post ORM object to a JSON-safe dict."""
    settings = get_settings()

    # Get media for this post
    media_items = db.query(Media).filter_by(post_id=post.id).all()

    media_list = []
    for m in media_items:
        media_list.append({
            "id": m.id,
            "url": m.url,
            "file_path": m.file_path,
            "thumb_path": m.thumb_path,
            "status": m.status,
            "media_url": _build_media_url(m.file_path, settings.archive_path) if m.file_path else None,
            "thumb_url": _build_thumb_url(m.thumb_path, settings.thumb_path) if m.thumb_path else None,
        })

    result = {
        "id": post.id,
        "subreddit": post.subreddit,
        "author": post.author,
        "title": post.title,
        "selftext": post.selftext,
        "url": post.url,
        "media_url": post.media_url,
        "created_utc": post.created_utc.isoformat() if post.created_utc else None,
        "ingested_at": post.ingested_at.isoformat() if post.ingested_at else None,
        "hidden": post.hidden,
        "media": media_list,
        "media_count": len(media_list),
    }

    if include_comments:
        comments = (
            db.query(Comment)
            .filter_by(post_id=post.id)
            .order_by(Comment.created_utc)
            .all()
        )
        result["comments"] = [
            {
                "id": c.id,
                "author": c.author,
                "body": c.body,
                "created_utc": c.created_utc.isoformat() if c.created_utc else None,
            }
            for c in comments
        ]

    return result


def _build_media_url(file_path: str, archive_path: str) -> Optional[str]:
    """Convert an absolute file path to a /media/ URL."""
    if not file_path:
        return None
    if file_path.startswith(archive_path):
        relative = file_path[len(archive_path):].lstrip("/")
        return f"/media/{relative}"
    return None


def _build_thumb_url(thumb_path: str, thumb_base: str) -> Optional[str]:
    """Convert an absolute thumb path to a /thumb/ URL."""
    if not thumb_path:
        return None
    if thumb_path.startswith(thumb_base):
        relative = thumb_path[len(thumb_base):].lstrip("/")
        return f"/thumb/{relative}"
    return None

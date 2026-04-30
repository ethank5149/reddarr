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
    # Offset-based pagination (used by frontend)
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
    sort: str = Query("newest", pattern="^(newest|oldest|score|comments|media_count)$"),
    # sort_by/sort_order used by frontend
    sort_by: Optional[str] = None,
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    show_hidden: bool = False,
    excluded: bool = False,  # frontend alias for show_hidden=True (archive view)
    has_media: Optional[bool] = None,
    media_type: Optional[str] = Query(None, pattern="^(video|image|text)$"),
    nsfw: str = Query("include", pattern="^(include|exclude)$"),
    db: Session = Depends(get_db),
):
    """List posts with pagination and filtering.

    This replaces the /api/posts endpoint from web/app.py,
    using SQLAlchemy ORM queries instead of raw SQL.

    Features:
    - Pagination (page-based or cursor-based)
    - Filtering by subreddit, author, media type, NSFW
    - Multiple sort options (newest, oldest, score, comments, media_count)
    - Video URL extraction from raw JSON
    - Remote media fallback (media_metadata, preview)
    """
    query = db.query(Post)

    # `excluded=True` means show only hidden posts (archive view)
    if excluded:
        query = query.filter(Post.hidden.is_(True))
    elif not show_hidden:
        query = query.filter(Post.hidden.is_(False))

    if subreddit:
        query = query.filter(func.lower(Post.subreddit) == subreddit.lower())

    if author:
        query = query.filter(func.lower(Post.author) == author.lower())

    # NSFW filter
    if nsfw == "exclude":
        from sqlalchemy import or_

        query = query.filter(or_(Post.raw.is_(None), Post.raw["over_18"].is_(False)))

    # Media type filtering
    if media_type:
        if media_type == "video":
            query = query.filter(
                or_(
                    Post.url.like("%v.redd.it%"),
                    Post.url.like("%youtube.com%"),
                    Post.url.like("%youtu.be%"),
                    Post.raw["media"]["reddit_video"].isnot(None),
                )
            )
        elif media_type == "image":
            query = query.filter(
                or_(
                    Post.url.like("%i.redd.it%"),
                    Post.url.like("%i.imgur.com%"),
                    Post.raw["media_metadata"].isnot(None),
                    Post.raw["preview"]["images"].isnot(None),
                )
            )
        elif media_type == "text":
            from sqlalchemy import and_

            query = query.filter(
                and_(
                    Post.url.is_(None),
                    Post.raw["media"].is_(None),
                    Post.raw["media_metadata"].is_(None),
                )
            )
    elif has_media is True:
        from sqlalchemy import or_

        query = query.filter(or_(Post.url.isnot(None), Post.media_url.isnot(None)))
    elif has_media is False:
        query = query.filter(Post.url.is_(None))

    # Sort - support both sort_by/sort_order (frontend) and sort (direct API)
    if sort_by:
        col_map = {
            "created_utc": Post.created_utc,
            "ingested_at": Post.ingested_at,
            "title": Post.title,
        }
        col = col_map.get(sort_by, Post.created_utc)
        query = query.order_by(col if sort_order == "asc" else desc(col))
    elif sort == "newest":
        query = query.order_by(desc(Post.created_utc))
    elif sort == "oldest":
        query = query.order_by(Post.created_utc)
    elif sort == "score":
        from sqlalchemy import Integer
        query = query.order_by(desc(Post.raw["score"].cast(Integer).label("score")))
    elif sort == "media_count":
        query = query.order_by(desc(Post.created_utc))
    else:
        query = query.order_by(desc(Post.ingested_at))

    # Paginate - offset-based (frontend) takes priority over page-based
    # Note: query.count() executes a separate COUNT query, which is necessary
    # for pagination. This is the standard SQLAlchemy pattern.
    total = query.count()

    if limit is not None:
        posts = query.offset(offset).limit(limit).all()
        effective_limit = limit
        effective_offset = offset
    else:
        effective_limit = per_page
        effective_offset = (page - 1) * per_page
        posts = query.offset(effective_offset).limit(effective_limit).all()

    results = []
    settings = get_settings()
    for p in posts:
        serialized = _serialize_post_enhanced(p, db, settings)
        results.append(serialized)

    return {
        "posts": results,
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
        db.query(PostHistory).filter_by(post_id=post_id).order_by(desc(PostHistory.version)).all()
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

    query = db.query(Post).filter(Post.tsv.op("@@")(tsquery)).filter(Post.hidden.is_(False))

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
    post.hidden_at = datetime.now(timezone.utc)
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

    # Get comment IDs before deleting comments
    comment_ids = [c.id for c in db.query(Comment).filter_by(post_id=post_id).all()]

    # Delete DB records (proper order: media, comments, histories, post)
    db.query(Media).filter_by(post_id=post_id).delete()
    db.query(Comment).filter_by(post_id=post_id).delete()

    # Delete history records
    db.query(PostHistory).filter_by(post_id=post_id).delete()
    if comment_ids:
        db.query(CommentHistory).filter(CommentHistory.comment_id.in_(comment_ids)).delete()

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
        media_list.append(
            {
                "id": m.id,
                "url": m.url,
                "file_path": m.file_path,
                "thumb_path": m.thumb_path,
                "status": m.status,
                "media_url": _build_media_url(m.file_path, settings.archive_path)
                if m.file_path
                else None,
                "thumb_url": _build_thumb_url(m.thumb_path, settings.thumb_path)
                if m.thumb_path
                else None,
            }
        )

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
        comments = db.query(Comment).filter_by(post_id=post.id).order_by(Comment.created_utc).all()
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
        relative = file_path[len(archive_path) :].lstrip("/")
        return f"/media/{relative}"
    return None


def _build_thumb_url(thumb_path: str, thumb_base: str) -> Optional[str]:
    """Convert an absolute thumb path to a /thumb/ URL."""
    if not thumb_path:
        return None
    if thumb_path.startswith(thumb_base):
        relative = thumb_path[len(thumb_base) :].lstrip("/")
        return f"/thumb/{relative}"
    return None


def _serialize_post_enhanced(post: Post, db: Session, settings) -> dict:
    """Enhanced post serialization with video extraction and remote media fallback.

    Mirrors the old web/app.py /api/posts endpoint's response format.
    """
    from reddarr.services.media import is_video_url

    media_items = db.query(Media).filter_by(post_id=post.id).all()

    # Build local URLs from downloaded files
    image_urls = []
    video_urls = []
    thumb_url = None

    for m in media_items:
        if m.file_path:
            local_url = _build_media_url(m.file_path, settings.archive_path)
            if local_url:
                if m.file_path.lower().endswith((".mp4", ".webm", ".mkv", ".mov", ".avi")):
                    video_urls.append(local_url)
                else:
                    image_urls.append(local_url)
        if m.thumb_path and not thumb_url:
            thumb_url = _build_thumb_url(m.thumb_path, settings.thumb_path)

    # Check raw JSON for embedded media
    is_video = is_video_url(post.url)
    if post.raw:
        try:
            if post.raw.get("media") and post.raw["media"].get("reddit_video"):
                is_video = True
        except Exception:
            pass

    preview_url = None
    remote_image_urls = []
    remote_video_urls = []

    # Extract remote media from raw JSON as fallback
    if post.raw and not image_urls and not video_urls:
        try:
            data = post.raw
            # Extract preview thumbnail
            if "preview" in data and not thumb_url:
                for img in data.get("preview", {}).get("images", []):
                    u = img.get("source", {}).get("url")
                    if u:
                        preview_url = u
                        break

            # For videos: use remote as fallback
            if is_video and not video_urls:
                extracted = _extract_video_url(post.url, data)
                if extracted:
                    remote_video_urls.append(extracted)
                elif post.url:
                    remote_video_urls.append(post.url)

            # For images: collect from media_metadata
            if not image_urls:
                if "media_metadata" in data:
                    for img_id, img_data in data.get("media_metadata", {}).items():
                        if "s" in img_data:
                            u = img_data["s"].get("u")
                            if u:
                                remote_image_urls.append(u.replace("&amp;", "&"))
                        elif img_data.get("p"):
                            u = img_data["p"][-1].get("u")
                            if u:
                                remote_image_urls.append(u.replace("&amp;", "&"))
                if not remote_image_urls and "preview" in data:
                    for img in data.get("preview", {}).get("images", []):
                        u = img.get("source", {}).get("url")
                        if u:
                            remote_image_urls.append(u.replace("&amp;", "&"))
                         # Note: Reddit API variants keys are 'gif', 'mp4', 'nsfw', 'obfuscated'
                         # The 'n' key doesn't exist in the API

                if remote_image_urls:
                    image_urls = remote_image_urls
        except Exception as e:
            logger.warning(f"Error parsing raw for {post.id}: {e}")

    # Deduplicate
    video_urls = list(dict.fromkeys([v.replace("&amp;", "&") for v in video_urls if v]))
    image_urls = list(dict.fromkeys([i.replace("&amp;", "&") for i in image_urls if i]))

    # Extract selftext from raw if not in column
    selftext = post.selftext
    if not selftext and post.raw:
        selftext = post.raw.get("selftext", "")

    return {
        "id": post.id,
        "title": post.title,
        "image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "video_url": video_urls[0] if video_urls else None,
        "video_urls": video_urls,
        "is_video": is_video,
        "selftext": selftext,
        "subreddit": post.subreddit,
        "author": post.author,
        "created_utc": post.created_utc.isoformat() if post.created_utc else None,
        "ingested_at": post.ingested_at.isoformat() if post.ingested_at else None,
        "thumb_url": thumb_url,
        "preview_url": preview_url,
        "excluded": post.hidden,
    }


def _extract_video_url(url: str, raw: dict) -> Optional[str]:
    """Extract playable video URL from post data.

    Handles v.redd.it DASH playlists and crosspost videos.
    """
    if not url:
        return None
    if "v.redd.it" in url:
        if raw:
            media = raw.get("media") or {}
            rv = media.get("reddit_video") or {}
            fallback = rv.get("fallback_url")
            if fallback:
                return fallback.split("?")[0]
            for cp in raw.get("crosspost_parent_list", []):
                media2 = cp.get("media") or {}
                rv2 = media2.get("reddit_video") or {}
                fb2 = rv2.get("fallback_url")
                if fb2:
                    return fb2.split("?")[0]
        return url
    if "youtube.com" in url or "youtu.be" in url:
        return url
    return None

import os, json, hashlib, requests, subprocess, sys
import psycopg2, redis
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

db = psycopg2.connect(os.getenv("DB_URL"))
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
MEDIA_DIR = "/data"
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

print(f"MEDIA_DIR set to: {MEDIA_DIR}")

session = requests.Session()
session.headers.update(
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(8192), b""):
            h.update(c)
    return h.hexdigest()


def make_thumb(path):
    thumb = path + ".thumb.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", "scale=320:-1", "-frames:v", "1", thumb],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return thumb


def get_best_image_url(url):
    """Follow redirects and get highest resolution image URL"""
    try:
        r = session.head(url, allow_redirects=True, timeout=10)
        final_url = r.url

        if "i.redd.it" in final_url:
            base = final_url.split(".")[0]
            high_res = f"{base}AUTO.format.jpg"
            return high_res
        return final_url
    except Exception as e:
        print(f"Redirect follow error: {e}")
        return url


while True:
    _, data = rd.brpop("media_queue")
    item = json.loads(data)
    post_id = item.get("post_id")
    url = item.get("url")

    if not url:
        print(f"Skipping {post_id} - no URL")
        continue

    try:
        print(f"Processing: {url[:60]}...")

        if "i.redd.it" in url:
            url = get_best_image_url(url)
            print(f"  -> High-res: {url[:60]}...")

        if (
            any(url.endswith(x) for x in [".jpg", ".jpeg", ".png", ".webp", ".gif"])
            or "i.redd.it" in url
        ):
            r = session.get(url, stream=True, timeout=60)
            if r.status_code != 200:
                print(f"HTTP {r.status_code} for {url}")
                continue

            name = f"{post_id}_{url.split('/')[-1].split('?')[0][:100]}"
            path = f"{MEDIA_DIR}/{name}"

            downloaded = False
            for chunk in r.iter_content(8192):
                if not downloaded:
                    with open(path, "wb") as f:
                        f.write(chunk)
                    downloaded = True
                else:
                    with open(path, "ab") as f:
                        f.write(chunk)

            h = sha256(path)

            cur = db.cursor()
            cur.execute("SELECT file_path FROM media WHERE sha256=%s", (h,))
            existing = cur.fetchone()

            if existing:
                os.remove(path)
                path = existing[0]
            else:
                thumb = make_thumb(path)

        elif "v.redd.it" in url or "youtube.com" in url or "youtu.be" in url:
            subprocess.run(
                ["yt-dlp", "-o", f"{MEDIA_DIR}/%(id)s.%(ext)s", url, "--quiet"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Video downloaded: {url}")
            path = f"{MEDIA_DIR}/{post_id}_video"

        elif url.startswith("https://preview.redd.it/") or url.startswith(
            "https://external-preview"
        ):
            url = get_best_image_url(url)
            print(f"  -> Followed to: {url[:60]}...")
            r = session.get(url, stream=True, timeout=60)
            if r.status_code == 200:
                name = f"{post_id}_{url.split('/')[-1].split('?')[0][:100]}"
                path = f"{MEDIA_DIR}/{name}"
                for chunk in r.iter_content(8192):
                    with open(path, "ab") as f:
                        f.write(chunk)
                h = sha256(path)
                thumb = make_thumb(path)
            else:
                print(f"Preview HTTP {r.status_code}")
                continue

        else:
            print(f"External link, attempting media extraction: {url}")
            try:
                r = session.get(url, timeout=30)
                content_type = r.headers.get("content-type", "")
                if "image" in content_type:
                    ext = content_type.split("/")[-1].split(";")[0].strip()
                    path = f"{MEDIA_DIR}/{post_id}.{ext}"
                    with open(path, "wb") as f:
                        f.write(r.content)
                    h = sha256(path)
                    thumb = make_thumb(path)
                else:
                    print(f"  Not an image, skipping: {content_type}")
                    continue
            except Exception as e:
                print(f"  Failed to extract: {e}")
                continue

        cur = db.cursor()
        cur.execute(
            """
   INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
   VALUES(%s,%s,%s,%s,%s,%s,'done')
   ON CONFLICT DO NOTHING
   """,
            (
                post_id,
                url,
                path,
                thumb if "thumb" in locals() else None,
                h if "h" in locals() else None,
                datetime.utcnow(),
            ),
        )
        db.commit()
        print(f"Saved: {path}")

    except Exception as e:
        print(f"ERROR {post_id}: {e}")
        try:
            cur = db.cursor()
            cur.execute(
                "INSERT INTO media(post_id,url,status,retries) VALUES(%s,%s,'failed',1) ON CONFLICT (post_id) DO NOTHING",
                (post_id, url),
            )
            db.commit()
        except:
            pass

import os,json,hashlib,requests,subprocess
import psycopg2,redis
from datetime import datetime

db=psycopg2.connect(os.getenv("DB_URL"))
rd=redis.Redis(host=os.getenv("REDIS_HOST"))
MEDIA_DIR=os.getenv("ARCHIVE_PATH", "/data")

def sha256(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for c in iter(lambda:f.read(8192),b""): h.update(c)
 return h.hexdigest()

def make_thumb(path):
 thumb = path + ".thumb.jpg"
 subprocess.run([
   "ffmpeg","-y","-i",path,
   "-vf","scale=320:-1",
   "-frames:v","1",
   thumb
 ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 return thumb

while True:
 _,data=rd.brpop("media_queue")
 item=json.loads(data)
 url=item["url"]

 try:
  name=url.split("/")[-1][:200]
  path=f"{MEDIA_DIR}/{name}"

  if any(url.endswith(x) for x in [".jpg",".png",".webp"]):
    r=requests.get(url,stream=True,timeout=30)
    with open(path,"wb") as f:
      for c in r.iter_content(8192): f.write(c)

    h=sha256(path)

    cur=db.cursor()
    cur.execute("SELECT file_path FROM media WHERE sha256=%s",(h,))
    existing=cur.fetchone()

    if existing:
      os.remove(path)
      path=existing[0]

    thumb = make_thumb(path)

  else:
    subprocess.run(["yt-dlp","-o",f"{MEDIA_DIR}/%(id)s.%(ext)s",url])
    continue

  cur=db.cursor()
  cur.execute("""
  INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
  VALUES(%s,%s,%s,%s,%s,%s,'done')
  ON CONFLICT DO NOTHING
  """,(item["post_id"],url,path,thumb,h,datetime.utcnow()))
  db.commit()

 except Exception:
  pass

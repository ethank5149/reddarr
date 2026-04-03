from fastapi import FastAPI, Query
import psycopg2,os

app=FastAPI()
db=psycopg2.connect(os.getenv("DB_URL"))

@app.get("/api/posts")
def posts(limit:int=50, offset:int=0):
 cur=db.cursor()
 cur.execute("""
 SELECT p.id,p.title,m.thumb_path
 FROM posts p
 LEFT JOIN media m ON p.id=m.post_id
 ORDER BY p.created_utc DESC
 LIMIT %s OFFSET %s
 """,(limit,offset))
 return cur.fetchall()

@app.get("/api/search")
def search(q:str, limit:int=50):
 cur=db.cursor()
 cur.execute("""
 SELECT id,title FROM posts
 WHERE tsv @@ plainto_tsquery(%s)
 LIMIT %s
 """,(q,limit))
 return cur.fetchall()

@app.post("/api/tag")
def tag(post_id:str, tag:str):
 cur=db.cursor()

 cur.execute("INSERT INTO tags(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",(tag,))
 cur.execute("SELECT id FROM tags WHERE name=%s",(tag,))
 tag_id=cur.fetchone()[0]

 cur.execute("""
 INSERT INTO post_tags(post_id,tag_id)
 VALUES(%s,%s) ON CONFLICT DO NOTHING
 """,(post_id,tag_id))

 db.commit()
 return {"status":"ok"}

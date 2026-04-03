import os, psycopg2

print("Connecting to DB...")
db = psycopg2.connect(os.getenv("DB_URL"))
cur = db.cursor()

subreddits = os.getenv("REDDIT_TARGET_SUBREDDITS", "").split(",")
users = os.getenv("REDDIT_TARGET_USERS", "").split(",")

for s in subreddits:
    s = s.strip()
    if s:
        cur.execute("INSERT INTO targets(type,name) VALUES('subreddit',%s) ON CONFLICT(name) DO NOTHING", (s,))
        print(f"Inserted subreddit: {s}")

for u in users:
    u = u.strip()
    if u:
        cur.execute("INSERT INTO targets(type,name) VALUES('user',%s) ON CONFLICT(name) DO NOTHING", (u,))
        print(f"Inserted user: {u}")

db.commit()
cur.close()
db.close()
print("Done loading targets")

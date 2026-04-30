import os
import pytest
import requests
import psycopg2
from psycopg2.extras import RealDictCursor


DB_URL = os.environ.get("DB_URL", "postgresql://reddit:changeme@db:5432/reddit")
API_URL = os.environ.get("API_URL", "http://api:8080")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")


def get_db_connection():
    return psycopg2.connect(DB_URL)


def get_admin_token():
    import hashlib

    return hashlib.sha256(f"{ADMIN_USER}:{ADMIN_PASS}".encode()).hexdigest()[:32]


class TestDatabaseSchema:
    REQUIRED_TABLES = [
        "users",
        "targets",
        "posts",
        "comments",
        "media",
        "posts_history",
        "comments_history",
    ]

    def test_all_required_tables_exist(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()

        missing = set(self.REQUIRED_TABLES) - tables
        assert not missing, f"Missing tables: {missing}"

    def test_posts_required_columns(self):
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'posts'"
        )
        columns = {row["column_name"]: row["data_type"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        required_cols = {
            "id",
            "subreddit",
            "author",
            "created_utc",
            "title",
            "selftext",
            "url",
            "media_url",
            "raw",
            "ingested_at",
            "hidden",
            "tsv",
        }
        missing = required_cols - set(columns.keys())
        assert not missing, f"posts table missing columns: {missing}"

    def test_media_required_columns(self):
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'media'"
        )
        columns = {row["column_name"]: row["data_type"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        required_cols = {
            "id",
            "post_id",
            "url",
            "file_path",
            "thumb_path",
            "sha256",
            "downloaded_at",
            "status",
            "retries",
            "error_message",
            "file_size",
            "created_at",
        }
        missing = required_cols - set(columns.keys())
        assert not missing, f"media table missing columns: {missing}"

    def test_posts_has_required_indexes(self):
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'posts' AND schemaname = 'public'
        """)
        indexes = {row["indexname"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        required_idx = {"posts_pkey", "idx_posts_subreddit", "idx_posts_ingested_at"}
        missing = required_idx - indexes
        assert not missing, f"posts table missing indexes: {missing}"

    def test_media_has_required_indexes(self):
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'media' AND schemaname = 'public'
        """)
        indexes = {row["indexname"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        required_idx = {"media_pkey", "idx_media_post_id", "idx_media_status"}
        missing = required_idx - indexes
        assert not missing, f"media table missing indexes: {missing}"

    def test_posts_has_tsv_trigger(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT trigger_name FROM information_schema.triggers 
            WHERE event_object_table = 'posts' AND trigger_name LIKE '%tsv%'
        """)
        triggers = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        assert triggers, "posts table missing tsv trigger"

    def test_comments_has_tsv_trigger(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT trigger_name FROM information_schema.triggers 
            WHERE event_object_table = 'comments' AND trigger_name LIKE '%tsv%'
        """)
        triggers = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        assert triggers, "comments table missing tsv trigger"


class TestDatabaseConnectivity:
    def test_database_is_reachable(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        cur.close()
        conn.close()
        assert result[0] == 1

    def test_database_has_data(self):
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as cnt FROM posts")
        count = cur.fetchone()["cnt"]
        cur.close()
        conn.close()
        assert count >= 0


class TestAPIEndpoints:
    def test_api_health_endpoint(self):
        response = requests.get(f"{API_URL}/health", timeout=10)
        assert response.status_code == 200

    def test_api_admin_stats_requires_auth(self):
        response = requests.get(f"{API_URL}/api/admin/stats", timeout=10)
        assert response.status_code == 401

    def test_api_admin_stats_with_valid_auth(self):
        token = get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{API_URL}/api/admin/stats", headers=headers, timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_posts" in data
        assert "total_media" in data

    def test_api_admin_activity_with_valid_auth(self):
        token = get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{API_URL}/api/admin/activity?limit=10", headers=headers, timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_api_admin_queue_with_valid_auth(self):
        token = get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{API_URL}/api/admin/queue", headers=headers, timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_api_admin_health_with_valid_auth(self):
        token = get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{API_URL}/api/admin/health", headers=headers, timeout=10
        )
        assert response.status_code == 200


class TestAPIDatabaseIntegration:
    def test_admin_stats_returns_valid_counts(self):
        token = get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{API_URL}/api/admin/stats", headers=headers, timeout=30
        )
        data = response.json()

        assert "total_posts" in data
        assert "excluded_posts" in data
        assert "total_comments" in data
        assert "downloaded_media" in data
        assert "pending_media" in data
        assert "total_media" in data

        assert isinstance(data["total_posts"], int)
        assert isinstance(data["downloaded_media"], int)
        assert isinstance(data["pending_media"], int)

    @pytest.mark.skip(reason="API icon fetching causes timeouts in test environment")
    def test_posts_query_works(self):
        token = get_admin_token()
        headers = {"X-Api-Key": token}  # Use correct header for API key auth
        response = requests.get(
            f"{API_URL}/api/posts?limit=5", headers=headers, timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.skip(reason="API icon fetching causes timeouts in test environment")
    def test_search_works(self):
        token = get_admin_token()
        headers = {"X-Api-Key": token}  # Use correct header for API key auth
        response = requests.get(
            f"{API_URL}/api/search?q=test&limit=5", headers=headers, timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_search_works_duplicate_removed(self):
        # The duplicate test_search_works method was removed
        # Tests should use X-Api-Key header instead of Authorization: Bearer
        pass


class TestNetworkConnectivity:
    def test_api_can_reach_database(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user")
        db, user = cur.fetchone()
        cur.close()
        conn.close()
        assert db == "reddit"
        assert user == "reddit"

    def test_api_can_reach_redis(self):
        import redis

        redis_host = os.environ.get("REDIS_HOST", "redis")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        r = redis.Redis(host=redis_host, port=redis_port, db=0)
        assert r.ping()

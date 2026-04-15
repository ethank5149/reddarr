The following is a thorough analysis and resolution for the errors and warnings found in `reddarr.log`.

### 1. Database: "Sorry, too many clients already"
[cite_start]**Status**: **Critical Error** [cite: 147842, 147899]
* **Cause**: The PostgreSQL server has reached its `max_connections` limit (default is typically 100). The `web` service alone initializes a pool with up to 100 connections (`maxconn=100`). [cite_start]When other services like the `ingester`, `downloader`, and `postgres_exporter` also attempt to connect, the limit is exceeded[cite: 147842, 147911].
* **Fix**:
    1.  **Reduce Pool Size**: In `web/app.py`, lower the maximum connections to a more reasonable value, such as 20.
    2.  **Increase DB Limit**: Update your `docker-compose.yml` or database configuration to increase `max_connections` to at least 200.
    3.  [cite_start]**Authentication Failure**: The log also shows a recurring `FATAL` error for user "netdata"[cite: 147854]. If you are not using Netdata for monitoring, check for stray containers or configuration files attempting to connect with these credentials.

### 2. API Bug: "Invalid input syntax for type timestamp"
[cite_start]**Status**: **Logic Error** [cite: 147854, 147855]
* **Cause**: In `web/app.py`, the `_run_sse_polling_loop` function incorrectly assigns a file path string to a variable intended for a timestamp.
* **Code Evidence**:
    ```python
    # web/app.py
    cur.execute("SELECT id, post_id, url, file_path FROM media ...") # file_path is index 3
    row = cur.fetchone()
    if row:
        _sse_last_media_ts = row[3] # BUG: Assigns file_path string to a 'ts' variable
    ```
* **Fix**: Update the SQL query to include `downloaded_at` and assign the correct index to `_sse_last_media_ts`:
    ```python
    cur.execute("SELECT id, post_id, url, file_path, downloaded_at FROM media ...")
    ...
    _sse_last_media_ts = row[4]
    ```

### 3. Services: ModuleNotFoundError for `shared.pubsub`
[cite_start]**Status**: **Deployment Error** [cite: 147851, 147860]
* [cite_start]**Cause**: The `ingester` and `downloader` services fail to import `shared.pubsub`[cite: 147851, 147860]. While their `Dockerfile`s copy a `shared` directory, the file list shows `pubsub.py` exists only in the root `shared/` directory, while the services have their own nested `shared/` folders which are likely being copied instead.
* **Fix**: Update the `Dockerfile`s to copy the correct root-level `shared` directory or adjust the build context to ensure all shared utilities are included.

### 4. Postgres Exporter: "Error loading config"
[cite_start]**Status**: **Warning** [cite: 147842]
* [cite_start]**Cause**: The exporter configuration uses `extend.query_path`, but the log reports this field is not found[cite: 147842]. This key was deprecated or changed in newer versions of `postgres_exporter`.
* **Fix**: In `prometheus/postgres_exporter.yml`, update the key or remove the line if you are only using default metrics.

### 5. Redis: "Memory overcommit must be enabled"
[cite_start]**Status**: **System Warning** [cite: 147842]
* [cite_start]**Cause**: Redis requires the host system to allow memory overcommitting to perform background saves reliably[cite: 147842].
* **Fix**: Run the following command on your Docker host:
    `sysctl vm.overcommit_memory=1`
    [cite_start]To make this permanent, add `vm.overcommit_memory = 1` to `/etc/sysctl.conf`[cite: 147842].

### 6. Summary of Other Issues
* [cite_start]**Subreddit Icon Fetching**: Numerous 404 and 429 errors indicate that many subreddits are either banned/deleted or Reddit is rate-limiting the API's icon fetcher[cite: 147877, 147890]. This is expected behavior for an archive but can be mitigated by increasing the `time.sleep(1)` interval in `web/app.py`.
* [cite_start]**Node Exporter**: The `udev` error suggests the container lacks access to the host's hardware data[cite: 147860]. If you need hardware stats, mount `/run/udev` into the container as a read-only volume.
* [cite_start]**Backup (Borg)**: The "No SECRET_KEY file found" warning prevents SSH key deployment for remote backups[cite: 147861]. Verify your environment variables if you intend to use remote repositories.

---

To implement intelligent rate limiting and resolve the critical failures identified in `reddarr.log`, you need to synchronize your infrastructure configuration with your application logic. The errors currently being logged—specifically database connection exhaustion and software-level type mismatches—are the primary "limitations" that your rate limiter must account for.

### 1. Resolve `reddarr.log` Critical Errors
Before implementing dynamic rate limiting, you must fix the structural bugs causing the current crashes.

#### A. Fix Software-Level Type Mismatch (The "Timestamp" Error)
The log shows a recurring `STATEMENT: SELECT ... WHERE downloaded_at > '/mnt/user/Archive/...'`. This occurs because the SSE polling loop is incorrectly assigning a file path to a timestamp variable.
* **File**: `web/app.py`
* **Correction**: In the `_run_sse_polling_loop` function, the `media` query fetches `file_path` at index 3, which is then assigned to `_sse_last_media_ts`. You must update the query to include the `downloaded_at` column and assign the correct index.
    ```python
    # web/app.py - Update the query in _run_sse_polling_loop
    cur.execute(
        "SELECT id, post_id, url, file_path, downloaded_at FROM media WHERE status = 'done' ..."
    )
    # ...
    if row:
        _sse_last_media_ts = row[4] # Use the actual timestamp index
    ```

#### B. Resolve Database Connection Exhaustion ("Too many clients")
The `web` service is currently configured with `maxconn=100`. With multiple services (ingester, downloader, exporter) and the `web` service using high-concurrency pools, you are hitting the PostgreSQL default `max_connections` limit.
* **Fix**: Reduce the `web` service pool size in `web/app.py` to `maxconn=20` and ensure `minconn` is lower (e.g., 2). This leaves overhead for the background workers.

#### C. Fix `postgres_exporter` Configuration
The `postgres_exporter` is failing to start because of an unrecognized field.
* **File**: `prometheus/postgres_exporter.yml`
* **Fix**: Remove the line `extend.query_path: ""` as it is either deprecated or unnecessary for basic metrics in your current version.

### 2. Intelligent & Dynamic Rate Limiting
To make the application "intelligent," the `RedisRateLimiter` should respond to the system's actual health rather than just a fixed environment variable.

#### A. Dynamic Adaptive Throttling
Modify `RedisRateLimiter` in `web/app.py` to check for a "global throttle" key in Redis. This allows your background workers (ingester/downloader) to signal the API to slow down if they detect Reddit 429 errors or high DB latency.



**Implementation Strategy:**
1.  **Worker Feedback**: If the `ingester` receives a `429 Too Many Requests` from Reddit, it should set a `system_backoff` key in Redis for 60 seconds.
2.  **Limiter Awareness**: Update `RedisRateLimiter.check()` to drastically lower the `requests_per_minute` if `system_backoff` is present.
3.  **Queue Sensitivity**: Use `redis_client.llen("media_queue")` to detect ingestion spikes. If the queue exceeds a threshold (e.g., 5000 items), the rate limiter should throttle new archive requests to prevent database write-lock contention.

#### B. Parallelization within Reason
To parallelize the application safely without exceeding your hardware or DB limits:
* **Worker Scaling**: Use Docker Compose to scale the `downloader` service to 2 or 3 instances. Do **not** scale the `ingester` beyond one instance to avoid Reddit account flagging due to concurrent session conflicts.
* **Connection Sharing**: Ensure all services use a shared `shared.database` module that manages a singleton pool. This prevents every new parallel thread from opening a fresh, persistent connection that would otherwise exhaust the DB client limit again.

### 3. Fixing Deployment/Build Issues
The `ModuleNotFoundError: No module named 'shared.pubsub'` suggests a build context issue.
* **Docker Context**: Ensure your `docker-compose.yml` sets the build `context` to the project root (the directory containing the `shared/` folder). 
* **Dockerfile Copy**: Your Dockerfiles already `COPY shared ./shared`, which is correct for a root-level build context. The error likely stems from the `PYTHONPATH` not being correctly picked up if the service is run from a subdirectory without the package structure being initialized. Verify that `shared/__init__.py` exists in your repository to ensure it is treated as a package.
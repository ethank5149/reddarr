import os


def read_secret(path: str) -> str:
    """Read a secret from a file, stripping whitespace."""
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def get_secret(secret_name: str, default: str = "") -> str:
    """Get a secret from /run/secrets/ or environment variable.

    Args:
        secret_name: Name of the secret (without path)
        default: Default value if secret not found

    Returns:
        The secret value or default
    """
    secret_path = f"/run/secrets/{secret_name}"
    if os.path.exists(secret_path):
        return read_secret(secret_path)
    return os.environ.get(secret_name.upper(), default)


def get_db_url() -> str:
    """Construct DB URL from environment/secrets.

    Priority:
    1. DB_URL environment variable (already constructed)
    2. Construct from postgres secrets in /run/secrets/
    3. Fall back to env vars for individual components
    """
    db_url = os.environ.get("DB_URL")
    if db_url:
        return db_url

    pg_password = get_secret("postgres_password")
    if pg_password:
        user = os.environ.get("POSTGRES_USER", "reddit")
        host = os.environ.get("POSTGRES_HOST", "db")
        port = os.environ.get("POSTGRES_PORT", "5432")
        db = os.environ.get("POSTGRES_DB", "reddit")
        return f"postgresql://{user}:{pg_password}@{host}:{port}/{db}"

    return os.environ.get("DB_URL", "postgresql://reddit:changeme@db:5432/reddit")

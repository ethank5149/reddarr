import os


def load_targets():
    """Load subreddits and users from file.

    TARGETS_FILE env var must be set. File format (one target per line, empty lines ignored):
        subreddit:python
        user:spez
        subreddit:learnprogramming

    Returns:
        tuple: (subreddits list, users list)
    """
    targets_file = os.getenv("TARGETS_FILE")

    if not targets_file:
        raise ValueError("TARGETS_FILE environment variable must be set")

    subreddits = []
    users = []
    try:
        with open(targets_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    ttype, name = line.split(":", 1)
                    ttype = ttype.lower().strip()
                    name = name.strip()
                    if ttype == "subreddit":
                        subreddits.append(name)
                    elif ttype == "user":
                        users.append(name)
    except FileNotFoundError:
        raise ValueError(f"Targets file not found: {targets_file}")

    return subreddits, users

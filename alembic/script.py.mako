"""Alembic configuration for Reddarr"""
from alembic import context as alembic_context

config = alembic_context.config

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alembic import script as alembic_script
from alembic import environment as alembic_env

def get_revision():
    return alembic_script.ScriptDirectory.from_config(config).head


def get_migration_description():
    return "Reddarr database migrations"


def get_url():
    from shared.config import get_db_url
    return get_db_url()
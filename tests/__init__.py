"""Tests run against the compose services, on their own Postgres database and Redis db.

This runs before anything under ``app`` is imported, so every ``get_settings()`` sees it. The
values are forced, not defaulted, so a developer's own environment never points tests at dev data.
"""

from __future__ import annotations

import os

os.environ['INA_DATABASE_URL'] = os.environ.get(
    'INA_TEST_DATABASE_URL', 'postgresql+asyncpg://ina:ina@localhost:15432/ina_test'
)
os.environ['INA_REDIS_URL'] = os.environ.get('INA_TEST_REDIS_URL', 'redis://localhost:6379/1')
os.environ['INA_SEARCH_PREFIX'] = 'ina_test'
os.environ['INA_SEARCH_REFRESH'] = 'true'
os.environ['INA_S3_BUCKET'] = 'ina-test'
# Tests that want thumbnails turn this on and serve the pages themselves.
os.environ['INA_THUMBNAILS'] = 'false'
# The thumbnail tests serve their pages from this machine.
os.environ['INA_MEDIA_ALLOW_PRIVATE'] = 'true'
# Cheap hashing: the cost is a deployment setting, not something the tests check.
os.environ['INA_PASSWORD_TIME_COST'] = '1'
os.environ['INA_PASSWORD_MEMORY_KIB'] = '1024'
os.environ['INA_PASSWORD_PARALLELISM'] = '1'

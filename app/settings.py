"""Environment-driven configuration, read once per process."""

from __future__ import annotations

from functools import cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='INA_', env_file='.env', frozen=True)

    debug: bool = False
    # Absolute base URL for links that leave the site, such as password reset emails.
    site_url: str = 'http://localhost'
    # Signs session cookies and password reset tokens. Set a long random value outside dev.
    secret_key: SecretStr = SecretStr('dev-only-secret-key-change-me')
    # Marks session cookies Secure. Off only for plain-HTTP local development.
    secure_cookies: bool = False

    database_url: str = 'postgresql+asyncpg://ina:ina@localhost:15432/ina'
    redis_url: str = 'redis://localhost:6379/0'
    # 127.0.0.1, not localhost: pika tries ::1 first and logs an error each time it is refused.
    amqp_url: str = 'amqp://ina:ina@127.0.0.1:5672/'

    opensearch_url: str = 'http://localhost:9200'
    # Index names start with this, so tests and several checkouts can share one cluster.
    search_prefix: str = 'ina'
    # Make each write searchable before it returns. Slow; for tests.
    search_refresh: bool = False

    # Object storage: uploaded images and thumbnails, under hashed names.
    s3_endpoint: str = 'http://127.0.0.1:8333'
    s3_bucket: str = 'ina'
    s3_region: str = 'us-east-1'
    s3_access_key: str = 'ina'
    s3_secret_key: SecretStr = SecretStr('ina-dev-secret')
    # Where browsers fetch those objects. /media is served by the app itself; production can
    # point this at the edge or a CDN in front of the bucket.
    media_url: str = '/media'
    # Make thumbnails for new links by fetching what they point to.
    thumbnails: bool = True
    # Find players for new links to listed video, audio and social sites (app/services/embeds.py).
    embeds: bool = True
    # Let that fetching reach private addresses. Only for tests, which serve pages locally.
    media_allow_private: bool = False

    smtp_host: str = 'localhost'
    smtp_port: int = 1025
    mail_from: str = 'ina <noreply@localhost>'

    # Where this site's source is published; the footer links to it, as the AGPL asks that anyone
    # who uses the site over a network be offered its source. A modified deployment points this
    # at its own changes.
    source_url: str = 'https://github.com/ina-pub/ina'

    # The logged-out front page, by name. Empty means the biggest listed public communities.
    default_communities: tuple[str, ...] = ()

    # Who may create communities, as r2's live config set it.
    create_community_account_age_days: int = 0
    create_community_link_karma: int = 0
    create_community_comment_karma: int = 0

    # argon2id cost; tune so one hash takes about 250 ms on production hardware.
    password_time_cost: int = 24
    password_memory_kib: int = 65536
    password_parallelism: int = 4


@cache
def get_settings() -> Settings:
    return Settings()

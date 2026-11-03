# Point the app at the test database, Redis db 1 and the test bucket, so a run never touches dev
# data. The test suite truncates these tables, so anything seeded here is disposable.
export INA_DATABASE_URL='postgresql+asyncpg://ina:ina@localhost:15432/ina_test'
export INA_REDIS_URL='redis://localhost:6379/1'
export INA_SEARCH_PREFIX=ina_test
export INA_S3_BUCKET=ina-test
# The server must not queue jobs on the shared RabbitMQ (a dev worker would run them against the
# dev database); seed.py runs the media jobs itself.
export INA_THUMBNAILS=false
export INA_EMBEDS=false
export INA_SITE_URL='http://localhost:8765'
export INA_RUN_URL='http://localhost:8765'

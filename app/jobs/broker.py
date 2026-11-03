"""The RabbitMQ broker every actor publishes to and consumes from."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import AsyncIO, Retries, default_middleware

from app.settings import get_settings

MAX_RETRIES = 5

broker = RabbitmqBroker(
    url=get_settings().amqp_url,
    middleware=[
        *(
            Retries(max_retries=MAX_RETRIES) if middleware is Retries else middleware()
            for middleware in default_middleware
        ),
        AsyncIO(),
    ],
)
dramatiq.set_broker(broker)

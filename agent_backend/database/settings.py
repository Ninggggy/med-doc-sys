import os

from agent.agent_backend.config.settings import settings as app_settings
from agent.agent_backend.config.settings import _normalize_runtime_host


HOST = "127.0.0.1"

ES = {
    "hosts": str(app_settings.es_url or f"http://{HOST}:9200"),
    "username": os.getenv("ES_USERNAME", "elastic"),
    "password": os.getenv("ES_PASSWORD", ""),
}

REDIS = {
    "host": str(app_settings.redis_host or HOST),
    "port": int(os.getenv("REDIS_PORT", str(getattr(app_settings, "redis_port", 6379)))),
    "db": int(os.getenv("REDIS_DB", str(getattr(app_settings, "redis_db", 0)))),
    "username": os.getenv("REDIS_USERNAME", "default"),
    "password": os.getenv("REDIS_PASSWORD", ""),
}

MYSQL = {
    "host": _normalize_runtime_host(os.getenv("MYSQL_HOST", HOST)),
    "port": int(os.getenv("MYSQL_SERVER_PORT", os.getenv("MYSQL_PORT", "3306"))),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "pre_review_db"),
    "pool_size": int(os.getenv("MYSQL_POOL_SIZE", "40")),
    "max_overflow": int(os.getenv("MYSQL_MAX_OVERFLOW", "20")),
}

SVR_QUEUE_NAME = os.getenv("SVR_QUEUE_NAME", "handle_info_queue")
SVR_QUEUE_RETENTION = int(os.getenv("SVR_QUEUE_RETENTION", str(60 * 60)))
SVR_QUEUE_MAX_LEN = int(os.getenv("SVR_QUEUE_MAX_LEN", "1024"))
SVR_CONSUMER_NAME = os.getenv("SVR_CONSUMER_NAME", "handle_info_consumer")
SVR_CONSUMER_GROUP_NAME = os.getenv("SVR_CONSUMER_GROUP_NAME", "handle_info_consumer_group")

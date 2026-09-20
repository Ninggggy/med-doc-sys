import logging
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from agent.agent_backend.config.settings import settings
from agent.agent_backend.database.settings import MYSQL
from agent.agent_backend.database import singleton


Base = declarative_base()


def _build_fallback_mysql_url() -> str:
    return (
        f"mysql+pymysql://{MYSQL['user']}:{MYSQL['password']}"
        f"@{MYSQL['host']}:{MYSQL['port']}/{MYSQL['database']}"
    )


def _running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    cgroup_path = Path("/proc/1/cgroup")
    try:
        if cgroup_path.exists():
            content = cgroup_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "docker" in content or "kubepods" in content or "containerd" in content:
                return True
    except Exception:
        return False
    return False


def _normalize_runtime_mysql_url(database_url: str) -> str:
    raw_url = str(database_url or "").strip()
    if not raw_url:
        return raw_url
    url = make_url(raw_url)
    if not str(url.drivername or "").startswith("mysql"):
        return raw_url
    host = str(url.host or "").strip().lower()
    if host != "mysql":
        return raw_url
    if _running_in_container():
        return raw_url
    fallback_host = str(os.getenv("MYSQL_LOCAL_FALLBACK_HOST", "127.0.0.1") or "").strip() or "127.0.0.1"
    normalized_url = url.set(host=fallback_host).render_as_string(hide_password=False)
    logging.warning(
        "MYSQL_URL host 'mysql' is a container service alias. Current process is not running in a container; "
        "fallback to %s.",
        fallback_host,
    )
    return normalized_url


def _resolve_database_url() -> str:
    # Priority:
    # 1) MYSQL_URL env var
    # 2) settings.mysql_url from .env
    # 3) fallback hardcoded MYSQL dict
    env_url = os.getenv("MYSQL_URL", "").strip()
    if env_url:
        return _normalize_runtime_mysql_url(env_url)
    cfg_url = str(getattr(settings, "mysql_url", "") or "").strip()
    if cfg_url:
        return _normalize_runtime_mysql_url(cfg_url)
    return _normalize_runtime_mysql_url(_build_fallback_mysql_url())


def _is_unknown_database_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "unknown database" in msg or "(1049" in msg


def _safe_db_name(name: str) -> str:
    if not re.match(r"^[A-Za-z0-9_]+$", name or ""):
        raise ValueError(f"Invalid database name: {name}")
    return name


def _create_mysql_database_if_needed(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("mysql"):
        return
    db_name = _safe_db_name(url.database or "")
    if not db_name:
        return

    server_url = URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=None,
        query=url.query,
    )

    engine = create_engine(server_url, echo=False, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        logging.warning("Database %s not found, created automatically.", db_name)
    finally:
        engine.dispose()


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    db_path = str(url.database or "").strip()
    if not db_path or db_path == ":memory:":
        return
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@singleton
class MysqlConnection:
    def __init__(self):
        self.database_url = _resolve_database_url()
        engine_options = {"pool_size": MYSQL["pool_size"], "max_overflow": MYSQL["max_overflow"]}
        # SQLite does not support pool_size/max_overflow.
        if self.database_url.startswith("sqlite"):
            engine_options = {}
            _ensure_sqlite_parent_dir(self.database_url)

        self.engine = create_engine(
            self.database_url,
            echo=False,
            pool_pre_ping=True,
            **engine_options,
        )
        try:
            Base.metadata.create_all(bind=self.engine)
        except OperationalError as e:
            # MySQL unknown database: create db and retry once.
            if _is_unknown_database_error(e):
                _create_mysql_database_if_needed(self.database_url)
                self.engine.dispose()
                self.engine = create_engine(
                    self.database_url,
                    echo=False,
                    pool_pre_ping=True,
                    **engine_options,
                )
                Base.metadata.create_all(bind=self.engine)
            else:
                raise

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        logging.info("MysqlConnection init: %s", self.database_url)

    def get_session(self):
        return self.SessionLocal()

    def recreate_all(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def get_table_structure_with_comments(self, table_name):
        inspector = inspect(self.engine)
        columns = inspector.get_columns(table_name)
        table_structure = {}
        for column in columns:
            column_name = column["name"]
            column_comment = column.get("comment", "No comment")
            if column_comment is None:
                column_comment = "No comment"
            table_structure[column_name] = column_comment
        return table_structure

import io
import threading
import time
import uuid
import warnings
import weakref
from collections import OrderedDict
from typing import Dict, Type, Union
from urllib.parse import quote_plus

import polars as pl
from django.conf import settings
from django.db import models
from sqlalchemy import MetaData, Table, cast, create_engine, event, literal, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.sql.schema import quoted_name
from typeguard import typechecked

# ----------------
# Per-process caches (D1/D2)
# ----------------
# SQLAlchemy engines are expensive to build and designed to be long-lived, so we
# cache one per (alias + resolved connection params). The SQLite engine is the
# deliberate exception: it is bound to Django's live connection via a creator and
# is rebuilt every call (see ``_get_sqlalchemy_engine``).
#
# The cache is bounded (LRU) because a caller may target an open-ended number of
# dynamically-provisioned databases; unbounded, every tenant ever touched would
# keep a connection pool alive for the life of the process.
_ENGINE_CACHE: "OrderedDict[tuple, object]" = OrderedDict()
_ENGINE_LOCK = threading.Lock()
DEFAULT_ENGINE_CACHE_SIZE = 32

# Reflected ``Table`` metadata is cached per engine. Cached engines keep their
# reflection warm across calls; the uncached SQLite engine gets a fresh metadata
# per call (keyed by the short-lived engine object), so reflection always matches
# the current schema even as tests create/drop tables.
_METADATA_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_REFLECT_LOCK = threading.RLock()

from ..polars_kit import mo_polars_kit
from ..response_kit import mo_validation_kit
from . import frame_stream
from .db_target import DbTargetLike, resolve_db_target


# ----------------
# Classes
# ----------------
@typechecked
class CRUDProcessor:
    """
    Execute bulk create/update database operations for validated model DataFrames.

    This processor is the persistence layer of the CRUD kit. It bridges Django model
    metadata with SQLAlchemy execution so validated Polars data can be written to the
    configured database backend using a unified interface across SQLite, PostgreSQL,
    and MySQL.

    Responsibilities:
    1. Resolve the target database (configured alias or explicit connection) to a
       SQLAlchemy engine.
    2. Perform append-style inserts for `create`.
    3. Perform upsert-style updates for `update`, with optional staging-table merge.
    4. Return operation metadata such as affected tables and execution time.
    """
    def __init__(
        self,
        model_frame_map: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        using: DbTargetLike = None,
    ):
        self.model_frame_map = model_frame_map
        self.target = resolve_db_target(using)
        self.db_alias = self.target.alias
        self.dialect = None
        self.engine = self._get_sqlalchemy_engine()

    def _get_sqlalchemy_engine(self):
        # Only the settings mapping is read up front. The live connection is
        # resolved inside the branches below, so an unsupported backend is
        # rejected by name without first importing that backend's driver.
        db = self.target.settings_dict
        engine = db["ENGINE"]

        if "sqlite" in engine:
            # Bound to Django's live connection via a creator, so intentionally
            # NOT cached: the underlying DBAPI connection can change between calls
            # (reconnects, per-test transactions) and a cached pool could go stale.
            self.dialect = "sqlite"
            django_connection = self.target.connection
            django_connection.ensure_connection()
            sqlite_engine = create_engine(
                "sqlite://", creator=lambda: django_connection.connection
            )
            # Django opens SQLite with ``isolation_level=None`` (pysqlite autocommit
            # mode). Because the DBAPI connection is owned by Django and handed to
            # SQLAlchemy through a creator, SQLAlchemy never emits a real ``BEGIN``,
            # so every statement in ``engine.begin()`` auto-commits — turning a bulk
            # insert into one fsync per row (thousands of disk syncs). Emitting an
            # explicit ``BEGIN`` on transaction start (the canonical pysqlite recipe)
            # makes the whole batch commit once, which is ~100x faster for bulk writes.
            event.listen(sqlite_engine, "begin", _sqlite_emit_begin)
            return sqlite_engine

        # ``self.dialect`` is the bare backend name ("mysql"/"postgresql"); the
        # "+driver" suffix lives only in the SQLAlchemy URL. The dialect-specific
        # upsert/merge/bulk-load branches compare against the bare name, so the
        # two must never be conflated.
        if "mysql" in engine:
            self.dialect = "mysql"
            driver = "mysql+pymysql"
            # ``local_infile`` enables the LOAD DATA LOCAL INFILE bulk-load path;
            # it transparently falls back to row binding when the server forbids
            # it (a common managed-MySQL restriction). See ``_load_data_mysql``.
            connect_args = {"local_infile": 1}
        elif "postgresql" in engine or "postgres" in engine:
            self.dialect = "postgresql"
            driver = "postgresql+psycopg2"
            connect_args = {}
        else:
            raise ValueError(f"Unsupported database engine: {engine}")

        user = quote_plus(db.get("USER", ""))
        password = quote_plus(db.get("PASSWORD", ""))
        host = db.get("HOST", "localhost")
        port = db.get("PORT", "")
        name = db["NAME"]
        cache_key = (self.db_alias, driver, name, user, password, host, port)

        with _ENGINE_LOCK:
            cached = _ENGINE_CACHE.get(cache_key)
            if cached is not None:
                _ENGINE_CACHE.move_to_end(cache_key)
                return cached
            auth_part = f"{user}:{password}@" if user or password else ""
            port_part = f":{port}" if port else ""
            self.target.connection.ensure_connection()
            sa_engine = create_engine(
                f"{driver}://{auth_part}{host}{port_part}/{name}",
                connect_args=connect_args,
            )
            _cache_engine(cache_key, sa_engine)
            return sa_engine

    def _reflect_table(self, conn, table_db: str):
        """Return a reflected ``Table`` for ``table_db``, cached per engine.

        Replaces the per-call ``inspect(conn).get_table_names()`` existence scan:
        reflection itself validates existence (a missing table raises a friendly
        error). Cached engines keep reflected metadata warm across calls; the
        uncached SQLite engine reflects fresh each call, so the cache never goes
        stale against the current schema.
        """
        metadata = _metadata_for_engine(self.engine)
        existing = metadata.tables.get(table_db)
        if existing is not None:
            return existing
        with _REFLECT_LOCK:
            existing = metadata.tables.get(table_db)
            if existing is not None:
                return existing
            try:
                return Table(
                    quoted_name(table_db, quote=True), metadata, autoload_with=conn
                )
            except NoSuchTableError as exc:
                raise ValueError(f"Table '{table_db}' does not exist.") from exc

    @staticmethod
    def _track_time(func):
        def wrapper(self, *args, **kwargs):
            start = time.time()
            try:
                affected_tables = func(self, *args, **kwargs)
                return {
                    "message": "Database Operation successful",
                    "affected_tables": affected_tables,
                    "time_taken_seconds": round(time.time() - start, 4),
                }
            except Exception as e:
                raise RuntimeError(f"CRUD operation failed: {e}") from e

        return wrapper

    @_track_time
    def create(self, batch_size: int) -> list[str]:
        """
        Insert each model DataFrame into its mapped database table.

        Rows are written ``batch_size`` at a time (``LazyFrame`` inputs are
        streamed through Parquet, eager frames are sliced). Each chunk is bound
        through SQLAlchemy Core ``insert()`` and sent via the DBAPI's
        ``executemany`` (one parameterized ``INSERT ... VALUES (...)`` template
        applied to the whole chunk), with dialect-correct type binding and no
        intermediate pandas conversion. (SQLAlchemy's ``insertmanyvalues``
        multi-row rewrite engages only for ``INSERT ... RETURNING``, which this
        append path does not use.)

        Args:
            batch_size: Maximum number of rows written per INSERT batch.

        Returns:
            list[str]: Database table names that were successfully written.
        """
        saved_tables = []
        with self.engine.begin() as conn:
            for model, df in self.model_frame_map.items():
                table_db = model._meta.db_table
                table = self._reflect_table(conn, table_db)
                self._fast_insert(df, table, conn, batch_size)
                saved_tables.append(table_db)
        return saved_tables

    @_track_time
    def update(self, batch_size: int) -> list[str]:
        """
        Upsert each model DataFrame into its mapped database table.

        Each frame is streamed into a per-call staging table using the same fast
        bulk loader as ``create()``, then merged into the target with a single
        set-based ``INSERT ... FROM SELECT ... ON CONFLICT/DUPLICATE`` statement —
        so the full frame is never materialized as Python rows.

        Args:
            batch_size: Maximum number of rows written per staging batch.

        Returns:
            list[str]: Database table names affected by the update operation.
        """
        affected_tables = []
        with self.engine.begin() as conn:
            # Local metadata only for staging tables: their names are unique per
            # call (``temp_upsert_<uuid>``), so they must never enter the shared
            # per-engine reflection cache.
            temp_metadata = MetaData()

            for model, df in self.model_frame_map.items():
                model_table = model._meta.db_table
                table = self._reflect_table(conn, model_table)
                pk_field = model._meta.pk.column or model._meta.pk.name
                pk_col = quoted_name(pk_field, quote=True)
                temp_table_name = f"temp_upsert_{uuid.uuid4().hex}"

                # Stage rows into the temp table in bounded-memory chunks; the
                # merge itself is set-based SQL.
                self._stream_write(df, temp_table_name, conn, batch_size)
                self._update__merge_staging(
                    table, temp_table_name, temp_metadata, pk_field, pk_col, conn
                )

                affected_tables.append(model_table)

        return affected_tables

    def _fast_insert(self, df, table, conn, batch_size: int) -> None:
        """Append a frame to an existing reflected table in bounded chunks.

        Each backend uses its fastest *same-connection* bulk loader, so the write
        stays inside the caller's single transaction (preserving ``create()``'s
        all-or-nothing guarantee) and Polars' columnar data is handed to the
        database without a detour through per-row Python objects:

        - PostgreSQL: ``COPY ... FROM STDIN`` streamed from an in-memory CSV
          buffer (``_copy_postgres``).
        - MySQL: ``LOAD DATA LOCAL INFILE`` from a temp CSV, auto-falling back to
          row binding when the server forbids ``LOCAL INFILE`` (``_load_data_mysql``).
        - SQLite: SQLAlchemy Core ``executemany`` — there is no bulk-load
          protocol, and this path is bound to Django's live connection so it
          works against an in-memory database.
        """
        for frm in self._iter_write_frames(df, batch_size):
            if frm.height == 0:
                continue
            if self.dialect == "postgresql":  # pragma: no cover - needs live PostgreSQL
                self._copy_postgres(table.name, frm, conn)
            elif self.dialect == "mysql":  # pragma: no cover - needs live MySQL
                self._load_data_mysql(table.name, frm, conn)
            else:
                conn.execute(table.insert(), frm.to_arrow().to_pylist())

    def _stream_write(self, df, table_name, conn, batch_size: int) -> None:
        """Append a frame to ``table_name`` (staging) in bounded-memory chunks.

        On PostgreSQL/MySQL the staging table is created once from the frame
        schema, then loaded with the same COPY / LOAD DATA bulk path as
        ``_fast_insert``. Other backends (SQLite) use Polars' SQLAlchemy writer,
        which creates the staging table on first append.
        """
        if self.dialect in ("postgresql", "mysql"):  # pragma: no cover - needs live DB
            self._fast_stream_write(df, table_name, conn, batch_size)
            return
        for frm in self._iter_write_frames(df, batch_size):
            frm.write_database(
                table_name=table_name,
                connection=conn,
                if_table_exists="append",
                engine="sqlalchemy",
            )

    def _fast_stream_write(self, df, table_name, conn, batch_size):  # pragma: no cover - needs live DB
        """Create the staging table from the frame schema, then bulk-load it."""
        created = False
        for frm in self._iter_write_frames(df, batch_size):
            if not created:
                # Cheaply materialize an empty table with the right columns/types,
                # then stream every chunk (this one included) through the loader.
                frm.clear().write_database(
                    table_name=table_name,
                    connection=conn,
                    if_table_exists="replace",
                    engine="sqlalchemy",
                )
                created = True
            if frm.height == 0:
                continue
            if self.dialect == "postgresql":
                self._copy_postgres(table_name, frm, conn)
            else:
                self._load_data_mysql(table_name, frm, conn)

    def _copy_postgres(self, table_name, frm, conn):  # pragma: no cover - needs live PostgreSQL
        """Bulk-load a chunk via ``COPY ... FROM STDIN`` on the same connection.

        ``quote_style="non_numeric"`` quotes every string (so an empty string is
        written as ``""``) while nulls are emitted as an unquoted empty field;
        PostgreSQL's CSV reader treats the former as ``''`` and the latter as
        ``NULL``, so the two never collide.
        """
        buffer = io.BytesIO()
        frm.write_csv(buffer, include_header=False, quote_style="non_numeric")
        buffer.seek(0)
        columns = ", ".join(f'"{name}"' for name in frm.columns)
        statement = f'COPY "{table_name}" ({columns}) FROM STDIN WITH (FORMAT CSV)'
        raw_cursor = conn.connection.dbapi_connection.cursor()
        try:
            raw_cursor.copy_expert(statement, buffer)
        finally:
            raw_cursor.close()

    def _load_data_mysql(self, table_name, frm, conn):  # pragma: no cover - needs live MySQL
        """Bulk-load a chunk via ``LOAD DATA LOCAL INFILE`` on the same connection.

        Nulls are written as the unquoted bareword ``NULL`` and every string is
        quoted, so MySQL's loader distinguishes a real ``NULL`` from the literal
        text ``"NULL"``. Falls back to Polars' SQLAlchemy row-binding writer when
        the server disallows ``LOCAL INFILE``.
        """
        tmp_path = frame_stream.new_temp_path(".csv")
        try:
            frm.write_csv(
                tmp_path,
                include_header=False,
                quote_style="non_numeric",
                null_value="NULL",
            )
            columns = ", ".join(f"`{name}`" for name in frm.columns)
            load_path = tmp_path.replace("\\", "\\\\").replace("'", "\\'")
            statement = (
                f"LOAD DATA LOCAL INFILE '{load_path}' INTO TABLE `{table_name}` "
                "FIELDS TERMINATED BY ',' ENCLOSED BY '\"' ESCAPED BY '' "
                f"LINES TERMINATED BY '\\n' ({columns})"
            )
            try:
                conn.exec_driver_sql(statement)
            except Exception:
                warnings.warn(
                    "LOAD DATA LOCAL INFILE is unavailable on this MySQL server "
                    "(local_infile may be disabled). Falling back to row-binding "
                    "writes — enable local_infile on the server for bulk-load "
                    "performance. See: https://dev.mysql.com/doc/refman/en/load-data-local-security.html",
                    RuntimeWarning,
                    stacklevel=4,
                )
                frm.write_database(
                    table_name=table_name,
                    connection=conn,
                    if_table_exists="append",
                    engine="sqlalchemy",
                )
        finally:
            _safe_unlink(tmp_path)

    def _iter_write_frames(self, df, batch_size: int):
        """Yield bounded-memory DataFrame chunks for writing.

        Thin delegate to :func:`frame_stream.iter_frames`, which the update
        back-fill shares — the chunking rules must not drift between the two.
        """
        yield from frame_stream.iter_frames(df, batch_size)

    def _update__merge_staging(
        self, table, temp_table_name, metadata, pk_field, pk_col, conn
    ):
        temp_table = Table(temp_table_name, metadata, autoload_with=conn)
        temp_select = select(temp_table)
        mo_validation_kit.ensure_in(
            self.dialect,
            ["sqlite", "postgresql", "mysql"],
            msg="No Valid Dialect Found for Update Operation",
            is_exception=True,
        )
        if self.dialect == "sqlite":
            mo_validation_kit.ensure_in(
                pk_col,
                table.c,
                msg=f"Primary key {pk_col!r} not found in {table.name}",
                is_exception=True,
            )
            other_cols = [c.name for c in table.columns if c.name != pk_col]
            if not other_cols:
                return
            col_names = [pk_col] + other_cols
            ordered_temp = self._ordered_staging_select(
                temp_select, table, col_names, cast_to_target=False
            ).where(literal(True))
            insert_stmt = sqlite_insert(table).from_select(col_names, ordered_temp)
            update_cols = {c: insert_stmt.excluded[c] for c in other_cols}
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[table.c[pk_col]], set_=update_cols
            )
            conn.execute(stmt)

        elif self.dialect == "mysql":
            col_names = [c.name for c in table.columns]
            ordered_temp = self._ordered_staging_select(
                temp_select, table, col_names, cast_to_target=True
            )
            insert_stmt = mysql_insert(table).from_select(col_names, ordered_temp)
            update_cols = {
                c.name: insert_stmt.inserted[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_duplicate_key_update(update_cols)
            conn.execute(stmt)

        elif self.dialect == "postgresql":
            col_names = [c.name for c in table.columns]
            ordered_temp = self._ordered_staging_select(
                temp_select, table, col_names, cast_to_target=True
            )
            insert_stmt = pg_insert(table).from_select(col_names, ordered_temp)
            update_cols = {
                c.name: insert_stmt.excluded[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk_col], set_=update_cols
            )
            conn.execute(stmt)

    @staticmethod
    def _ordered_staging_select(temp_select, table, col_names, *, cast_to_target):
        """Project the staging select onto ``col_names``, in that exact order.

        ``from_select`` pairs the target column list with the SELECT positionally,
        so the staging table's own column order must never be assumed to match the
        target table's. Without this projection a mismatch writes each value into
        the wrong column — loudly when the types disagree, silently when they do
        not.

        ``cast_to_target`` additionally casts each column to the target column's
        type. The staging table is created from the frame's Polars schema, so a
        UUID or timestamp lands there as text; strictly-typed backends refuse to
        merge that into a typed column, while SQLite's dynamic typing accepts it
        as-is and needs no cast.
        """
        columns = []
        for name in col_names:
            column = temp_select.c[name]
            if cast_to_target:
                column = cast(column, table.c[name].type)
            columns.append(column.label(name))
        return temp_select.with_only_columns(*columns)


# ----------------
# Functions
# ----------------
def _cache_engine(cache_key: tuple, sa_engine) -> None:
    """Cache ``sa_engine``, evicting the least recently used entry past the cap.

    Callers must already hold ``_ENGINE_LOCK``.

    A fixed set of configured databases never reaches the cap, so ordinary
    projects behave as before. The ceiling exists for callers that target many
    dynamically-provisioned databases: each engine owns a connection pool, and
    without eviction every database ever touched would hold one open for the life
    of the process. Evicted engines are disposed so their pooled sockets close.
    Set ``MO_CRUD_ENGINE_CACHE_SIZE`` to ``0`` to keep the cache unbounded.
    """
    _ENGINE_CACHE[cache_key] = sa_engine
    _ENGINE_CACHE.move_to_end(cache_key)
    max_size = getattr(
        settings, "MO_CRUD_ENGINE_CACHE_SIZE", DEFAULT_ENGINE_CACHE_SIZE
    )
    if not max_size or max_size <= 0:
        return
    while len(_ENGINE_CACHE) > max_size:
        _, evicted = _ENGINE_CACHE.popitem(last=False)
        dispose = getattr(evicted, "dispose", None)
        if callable(dispose):
            dispose()


def _sqlite_emit_begin(conn) -> None:
    """Emit an explicit ``BEGIN`` so SQLite writes share one transaction.

    Registered as the SQLAlchemy ``begin`` event for the Django-bound SQLite
    engine. Django's pysqlite connection runs in autocommit mode
    (``isolation_level=None``); without an explicit ``BEGIN`` each statement
    commits on its own, so a bulk insert fsyncs once per row. See
    ``_get_sqlalchemy_engine`` for the full rationale.
    """
    conn.exec_driver_sql("BEGIN")


def _metadata_for_engine(engine) -> MetaData:
    """Return the reflection ``MetaData`` bound to ``engine`` (created on first use).

    Keyed weakly by the engine object: cached engines keep their metadata for the
    process lifetime, while a short-lived (uncached SQLite) engine's metadata is
    collected with it — so reflection never outlives the connection it describes.
    """
    with _REFLECT_LOCK:
        metadata = _METADATA_CACHE.get(engine)
        if metadata is None:
            metadata = MetaData()
            _METADATA_CACHE[engine] = metadata
        return metadata


_safe_unlink = frame_stream.safe_unlink

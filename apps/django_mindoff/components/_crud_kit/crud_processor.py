import gc
import io
import os
import tempfile
import threading
import time
import uuid
import warnings
import weakref
from typing import Dict, Type, Union
from urllib.parse import quote_plus

import polars as pl
import pyarrow.parquet as pq
from django.conf import settings
from django.db import connection, models
from sqlalchemy import MetaData, Table, create_engine, event, literal, select
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
_ENGINE_CACHE: dict = {}
_ENGINE_LOCK = threading.Lock()

# Reflected ``Table`` metadata is cached per engine. Cached engines keep their
# reflection warm across calls; the uncached SQLite engine gets a fresh metadata
# per call (keyed by the short-lived engine object), so reflection always matches
# the current schema even as tests create/drop tables.
_METADATA_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_REFLECT_LOCK = threading.RLock()

from ..polars_kit import mo_polars_kit
from ..response_kit import mo_validation_kit


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
    1. Resolve the configured Django database alias to a SQLAlchemy engine.
    2. Perform append-style inserts for `create`.
    3. Perform upsert-style updates for `update`, with optional staging-table merge.
    4. Return operation metadata such as affected tables and execution time.
    """
    def __init__(
        self,
        model_frame_map: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        db_alias: str = "default",
    ):
        self.model_frame_map = model_frame_map
        self.db_alias = db_alias
        self.dialect = None
        self.engine = self._get_sqlalchemy_engine()

    def _get_sqlalchemy_engine(self):
        if self.db_alias not in settings.DATABASES:
            raise ValueError(
                f"Database alias '{self.db_alias}' not found in settings.DATABASES"
            )

        db = settings.DATABASES[self.db_alias]
        engine = db["ENGINE"]

        if "sqlite" in engine:
            # Bound to Django's live connection via a creator, so intentionally
            # NOT cached: the underlying DBAPI connection can change between calls
            # (reconnects, per-test transactions) and a cached pool could go stale.
            self.dialect = "sqlite"
            connection.ensure_connection()
            sqlite_engine = create_engine(
                "sqlite://", creator=lambda: connection.connection
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
                return cached
            auth_part = f"{user}:{password}@" if user or password else ""
            port_part = f":{port}" if port else ""
            connection.ensure_connection()
            sa_engine = create_engine(
                f"{driver}://{auth_part}{host}{port_part}/{name}",
                connect_args=connect_args,
            )
            _ENGINE_CACHE[cache_key] = sa_engine
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
        handle = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp_path = handle.name
        handle.close()
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

        ``LazyFrame`` inputs are streamed to a temporary Parquet file (bounded
        memory via the streaming engine) and re-read in Arrow batches, so the
        full result is never held in memory. Eager frames are sliced. At least
        one (possibly empty) frame is always yielded so the target table is
        created even for an empty result.
        """
        # Guard against a degenerate chunk size (``iter_slices``/streaming reader
        # require >= 1); a non-positive size means "no chunking", i.e. one row.
        batch_size = max(1, batch_size)
        if isinstance(df, pl.LazyFrame):
            yield from self._iter_lazy_frames(df, batch_size)
        elif df.height == 0:
            yield df
        else:
            yield from df.iter_slices(batch_size)

    def _iter_lazy_frames(self, df, batch_size: int):
        handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        tmp_path = handle.name
        handle.close()
        try:
            df.sink_parquet(tmp_path)
            with open(tmp_path, "rb") as file_handle:
                parquet = pq.ParquetFile(file_handle)
                wrote = False
                for batch in parquet.iter_batches(batch_size=batch_size):
                    wrote = True
                    yield pl.from_arrow(batch)
                if not wrote:
                    yield pl.from_arrow(parquet.schema_arrow.empty_table())
                del parquet
        finally:
            gc.collect()  # release the Parquet file handle (Windows) before unlink
            _safe_unlink(tmp_path)

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
            ordered_temp = temp_select.with_only_columns(
                *(temp_select.c[name] for name in col_names)
            ).where(literal(True))
            insert_stmt = sqlite_insert(table).from_select(col_names, ordered_temp)
            update_cols = {c: insert_stmt.excluded[c] for c in other_cols}
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[table.c[pk_col]], set_=update_cols
            )
            conn.execute(stmt)

        elif self.dialect == "mysql":
            insert_stmt = mysql_insert(table).from_select(
                [c.name for c in table.columns], temp_select
            )
            update_cols = {
                c.name: insert_stmt.inserted[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_duplicate_key_update(update_cols)
            conn.execute(stmt)

        elif self.dialect == "postgresql":
            insert_stmt = pg_insert(table).from_select(
                [c.name for c in table.columns], temp_select
            )
            update_cols = {
                c.name: insert_stmt.excluded[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk_col], set_=update_cols
            )
            conn.execute(stmt)


# ----------------
# Functions
# ----------------
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


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:  # pragma: no cover - best-effort cleanup
        pass

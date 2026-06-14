import gc
import os
import tempfile
import threading
import time
import uuid
import weakref
from typing import Dict, Type, Union
from urllib.parse import quote_plus

import polars as pl
import pyarrow.parquet as pq
from django.conf import settings
from django.db import connection, models
from sqlalchemy import MetaData, Table, create_engine, literal, select
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
            return create_engine("sqlite://", creator=lambda: connection.connection)

        if "mysql" in engine:
            self.dialect = "mysql+pymysql"
        elif "postgresql" in engine or "postgres" in engine:
            self.dialect = "postgresql+psycopg2"
        else:
            raise ValueError(f"Unsupported database engine: {engine}")

        user = quote_plus(db.get("USER", ""))
        password = quote_plus(db.get("PASSWORD", ""))
        host = db.get("HOST", "localhost")
        port = db.get("PORT", "")
        name = db["NAME"]
        cache_key = (self.db_alias, self.dialect, name, user, password, host, port)

        with _ENGINE_LOCK:
            cached = _ENGINE_CACHE.get(cache_key)
            if cached is not None:
                return cached
            auth_part = f"{user}:{password}@" if user or password else ""
            port_part = f":{port}" if port else ""
            connection.ensure_connection()
            sa_engine = create_engine(
                f"{self.dialect}://{auth_part}{host}{port_part}/{name}"
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
        streamed through Parquet, eager frames are sliced) using SQLAlchemy's
        batched multi-row INSERT (``insertmanyvalues``): PostgreSQL/SQLite emit
        multi-row ``INSERT ... VALUES`` and MySQL an optimized executemany, with
        dialect-correct type binding and no intermediate pandas conversion.

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
    def update(self, is_temp_table: bool, batch_size: int) -> list[str]:
        """
        Upsert each model DataFrame into its mapped database table.

        Args:
            is_temp_table: When true, stage rows into a temp table before merge.
            batch_size: Maximum number of rows written per staging/upsert batch.

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

                self._update__df_insert(
                    df,
                    is_temp_table=is_temp_table,
                    temp_table_name=temp_table_name,
                    table=table,
                    pk_field=pk_field,
                    pk_col=pk_col,
                    conn=conn,
                    batch_size=batch_size,
                )

                if is_temp_table:
                    self._update__merge_staging(
                        table, temp_table_name, temp_metadata, pk_field, pk_col, conn
                    )

                affected_tables.append(model_table)

        return affected_tables

    def _update__df_insert(
        self,
        df: pl.DataFrame | pl.LazyFrame,
        *,
        is_temp_table: bool,
        temp_table_name: str,
        table,
        pk_field: str,
        pk_col: str,
        conn,
        batch_size: int = 1000,
    ) -> None:
        if is_temp_table:
            # Stage rows into the temp table in bounded-memory chunks; the merge
            # itself is set-based SQL, so the full frame is never materialized.
            self._stream_write(df, temp_table_name, conn, batch_size)
            return
        # Direct dialect upsert: apply it one bounded chunk at a time.
        for frm in self._iter_write_frames(df, batch_size):
            rows = frm.to_arrow().to_pylist()
            if rows:
                self._upsert_rows(table, rows, pk_field, pk_col, conn)

    def _upsert_rows(self, table, rows, pk_field, pk_col, conn) -> None:
        if self.dialect == "postgresql":
            insert_stmt = table.insert().values(rows)
            update_cols = {
                c.name: insert_stmt.excluded[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk_col], set_=update_cols
            )
        elif self.dialect == "mysql":
            insert_stmt = table.insert().values(rows)
            update_cols = {
                c.name: insert_stmt.inserted[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_duplicate_key_update(update_cols)
        elif self.dialect == "sqlite":
            insert_stmt = sqlite_insert(table).values(rows)
            update_cols = {
                c.name: insert_stmt.excluded[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk_field], set_=update_cols
            )
        else:  # pragma: no cover - dialect already validated upstream
            return
        conn.execute(stmt)

    def _fast_insert(self, df, table, conn, batch_size: int) -> None:
        """Append a frame to an existing reflected table in bounded chunks.

        Each chunk is bound through SQLAlchemy Core ``insert()``, which SQLAlchemy
        2.x batches into a dialect-appropriate multi-row INSERT
        (``insertmanyvalues``). This reuses the same ``to_pylist`` + reflected-table
        binding as the direct upsert path, so type handling stays consistent.
        """
        for frm in self._iter_write_frames(df, batch_size):
            rows = frm.to_arrow().to_pylist()
            if rows:
                conn.execute(table.insert(), rows)

    def _stream_write(self, df, table_name, conn, batch_size: int) -> None:
        """Append a frame to ``table_name`` in bounded-memory chunks.

        Used for temp-table staging during updates: ``write_database`` creates the
        (not-yet-existing) staging table from the frame schema on first append.
        """
        for frm in self._iter_write_frames(df, batch_size):
            frm.write_database(
                table_name=table_name,
                connection=conn,
                if_table_exists="append",
                engine="sqlalchemy",
            )

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

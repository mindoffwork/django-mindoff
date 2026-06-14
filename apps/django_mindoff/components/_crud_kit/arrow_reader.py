"""
Read fast path: build Polars frames straight from the database instead of
materializing one Python ``dict`` per row through the ORM iterator.

Engines
-------
- ``"django"`` (also the ``"auto"`` default): execute the queryset's compiled
  SQL through Django's own cursor. Django handles placeholder conversion,
  parameter adaptation and transaction visibility on every backend, and the
  result is assembled column-wise -- Arrow-native when the driver cursor
  exposes ``fetch_arrow_table``, otherwise from row tuples (never
  list-of-dicts).
- ``"connectorx"``: hand the compiled SQL to ConnectorX via
  ``pl.read_database_uri`` for a zero-copy DB -> Arrow transfer. Opt-in:
  ConnectorX opens its own connection, so it cannot see uncommitted
  transaction state (and cannot reach an in-memory SQLite database).
- ``"iterator"``: the legacy ORM-iterator path. Kept as a guaranteed-compatible
  fallback and escape hatch; its output is left exactly as the ORM produced it.

Whatever the engine, frames from the ``django``/``connectorx`` paths are
normalized to the canonical dtypes in :data:`dtypes.DJANGO_TO_POLARS_TYPE_MAP`
so reads stay consistent with what the create/update validators expect.
"""

import atexit
import os
import pathlib
import tempfile
import warnings
import weakref
from itertools import islice
from urllib.parse import quote_plus

import orjson
import polars as pl
import pyarrow.parquet as pq
from django.conf import settings
from django.db import connections

from .dtypes import resolve_polars_dtype

# ----------------
# Constants
# ----------------
_UUID_FIELDS = ("UUIDField", "ForeignKey", "OneToOneField")
_UUID_HYPHENATE = r"^(.{8})(.{4})(.{4})(.{4})(.{12})$"


# ----------------
# Functions
# ----------------
def read_frame(qs, *, engine: str = "auto", json_column_mode: str = "auto"):
    """Return an eager ``pl.DataFrame`` for ``qs`` using the requested engine.

    ``qs`` must already be a ``.values()`` queryset and is expected to be sliced
    (LIMIT/OFFSET applied) by the caller for pagination. Unknown/failed fast-path
    engines fall back to the ORM iterator so a read never hard-fails on the
    fast path.
    """
    model = qs.model

    if engine == "iterator":
        return _execute_iterator(qs)

    if engine == "connectorx":
        frm = _try_connectorx(qs)
        if frm is None:
            frm = _execute_django(qs)
    else:  # "auto" / "django"
        try:
            frm = _execute_django(qs)
        except Exception as exc:  # pragma: no cover - defensive fallback
            warnings.warn(
                f"Read fast path failed ({type(exc).__name__}: {exc}); "
                "falling back to the ORM iterator.",
                RuntimeWarning,
            )
            return _execute_iterator(qs)

    return _normalize(frm, model, json_column_mode, engine)


def stream_batches(qs, *, batch_size: int, engine: str = "auto",
                   json_column_mode: str = "auto"):
    """Yield the queryset as a sequence of Polars frames, never concatenated.

    Memory-bounded: rows are pulled from the database one ``batch_size`` chunk at
    a time. The ``iterator`` engine streams ORM ``.values()`` dicts (output left
    as-is); every other engine streams Django's cursor with ``fetchmany`` and
    normalizes each batch to the canonical model dtypes. (ConnectorX has no
    streaming cursor, so it routes through the Django-cursor path here.)
    """
    if engine == "iterator":
        yield from _iterator_batches(qs, batch_size)
        return

    model = qs.model
    sql, params = qs.query.get_compiler(using=qs.db).as_sql()
    conn = connections[qs.db]
    conn.ensure_connection()
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        description = [col[0] for col in cursor.description]
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            frm = pl.from_records(rows, schema=description, orient="row")
            yield _normalize(frm, model, json_column_mode, engine)


def scan_to_lazy(qs, *, batch_size: int, engine: str = "auto",
                 json_column_mode: str = "auto") -> pl.LazyFrame:
    """Stream the queryset to a temp Parquet file and return a lazy scan.

    This is a genuine larger-than-RAM read: batches are written to disk
    incrementally (never all held in memory) and the returned ``LazyFrame``
    scans them on ``collect()``. The temp file is removed automatically when the
    ``LazyFrame`` is garbage-collected.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    tmp_path = handle.name
    handle.close()

    writer = None
    target_schema = None
    try:
        for frm in stream_batches(
            qs, batch_size=batch_size, engine=engine,
            json_column_mode=json_column_mode,
        ):
            if target_schema is None:
                target_schema = frm.schema
            else:
                frm = frm.cast(dict(target_schema))
            table = frm.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(tmp_path, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()

    if writer is None:  # empty result set: nothing was written
        _safe_unlink(tmp_path)
        return pl.LazyFrame()

    lazy = pl.scan_parquet(tmp_path)
    # Linux: finalize fires at GC time; unlink succeeds on an open file.
    # Windows: unlink fails while Polars holds the handle, so atexit provides
    # a guaranteed second attempt once the process has fully torn down.
    weakref.finalize(lazy, _safe_unlink, tmp_path)
    atexit.register(_safe_unlink, tmp_path)
    return lazy


def _iterator_batches(qs, batch_size: int):
    """Legacy streaming: ORM ``.values()`` dicts in chunks (output unchanged)."""
    it = qs.iterator(chunk_size=batch_size)
    while True:
        batch = list(islice(it, batch_size))
        if not batch:
            break
        yield pl.DataFrame(batch)


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:  # pragma: no cover - best-effort cleanup
        pass


def _field_lookup(model) -> dict:
    """Map every concrete field's name/attname/db-column to the field object."""
    lookup = {}
    for field in model._meta.concrete_fields:
        for key in (field.name, field.attname, field.column):
            if key:
                lookup[key] = field
    return lookup


def _execute_django(qs) -> pl.DataFrame:
    """Run the compiled SQL through Django's cursor and build a frame.

    Column names come from ``cursor.description``: Django aliases every selected
    column to its ``.values()`` key (``... AS "key"``), so these names match the
    ORM-iterator output exactly, in the correct order — even when an annotation
    is explicitly positioned within ``.values()``.
    """
    sql, params = qs.query.get_compiler(using=qs.db).as_sql()
    conn = connections[qs.db]
    conn.ensure_connection()
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        fetch_arrow = getattr(cursor, "fetch_arrow_table", None)
        if callable(fetch_arrow):  # pragma: no cover - driver-dependent
            return pl.from_arrow(fetch_arrow())
        rows = cursor.fetchall()
        description = [col[0] for col in cursor.description]
    if not rows:
        return pl.DataFrame(schema={name: pl.Null for name in description})
    return pl.from_records(rows, schema=description, orient="row")


def _execute_iterator(qs) -> pl.DataFrame:
    """Legacy path: materialize ORM ``.values()`` dicts into a frame."""
    return pl.DataFrame(list(qs))


def _try_connectorx(qs):
    """Read via ConnectorX, or return ``None`` to signal a fallback."""
    try:
        import connectorx  # noqa: F401
    except Exception:
        warnings.warn(
            "engine='connectorx' requested but connectorx is not importable; "
            "falling back to the Django cursor engine.",
            RuntimeWarning,
        )
        return None

    uri = _connectorx_uri(qs.db)
    if uri is None:
        warnings.warn(
            "engine='connectorx' is not available for this database "
            "(unsupported backend or in-memory SQLite); falling back to the "
            "Django cursor engine.",
            RuntimeWarning,
        )
        return None

    # ConnectorX uses its own connection and accepts only a raw SQL string, so
    # parameters are inlined via the query's string form. The SQL aliases each
    # column to its `.values()` key, so result column names already match.
    sql = str(qs.query)  # pragma: no cover - requires a live external DB
    return pl.read_database_uri(sql, uri)  # pragma: no cover


def _connectorx_uri(db_alias: str):
    """Build a ConnectorX connection URI from Django's database settings."""
    db = settings.DATABASES.get(db_alias, {})
    engine = db.get("ENGINE", "")
    name = db.get("NAME", "")

    if "sqlite" in engine:
        lowered = str(name).lower()
        # In-memory SQLite (including pytest-django's shared-cache named memory
        # database) cannot be reached over a separate ConnectorX connection.
        if not name or ":memory:" in lowered or "mode=memory" in lowered:
            return None
        # Convert to a forward-slash posix path so Windows backslashes don't
        # produce an invalid URI. "sqlite:///C:/path" is correct on Windows;
        # "sqlite:////abs/path" is correct on Linux — both use this form.
        posix = pathlib.Path(name).as_posix()
        return f"sqlite:///{posix}"

    user = quote_plus(str(db.get("USER", "")))
    password = quote_plus(str(db.get("PASSWORD", "")))
    host = db.get("HOST", "") or "localhost"
    port = db.get("PORT", "")
    auth = f"{user}:{password}@" if (user or password) else ""
    port_part = f":{port}" if port else ""

    if "postgresql" in engine or "postgres" in engine:  # pragma: no cover
        return f"postgresql://{auth}{host}{port_part}/{name}"
    if "mysql" in engine:  # pragma: no cover
        return f"mysql://{auth}{host}{port_part}/{name}"
    return None


def _normalize(
    frm: pl.DataFrame, model, json_column_mode: str, engine: str
) -> pl.DataFrame:
    """Coerce columns to the canonical model dtypes (no-op on empty frames)."""
    if frm.height == 0:
        return frm

    lookup = _field_lookup(model)
    schema = frm.schema
    exprs = []
    for column in frm.columns:
        field = lookup.get(column)
        if field is None:
            continue  # annotation / extra / unmapped column: leave inferred
        dtype = resolve_polars_dtype(field)
        if dtype is None:
            continue

        field_cls = field.__class__.__name__
        if field_cls in _UUID_FIELDS:
            exprs.append(_uuid_expr(column))
        elif field_cls == "JSONField":
            exprs.append(_json_expr(column, json_column_mode, engine))
        elif field_cls == "DateTimeField":
            exprs.append(_datetime_expr(column, schema[column]))
        else:
            exprs.append(pl.col(column).cast(dtype, strict=False))

    return frm.with_columns(exprs) if exprs else frm


def _uuid_expr(column: str) -> pl.Expr:
    """Render UUID/related columns as lowercase, hyphenated UUID strings.

    Matches how Django returns a ``UUIDField`` on read regardless of how the
    backend stored it (e.g. SQLite keeps the dashless 32-char hex form).
    """
    return (
        pl.col(column)
        .cast(pl.Utf8, strict=False)
        .str.to_lowercase()
        .str.replace_all("-", "")
        .str.replace_all(_UUID_HYPHENATE, r"$1-$2-$3-$4-$5")
        .alias(column)
    )


def _datetime_expr(column: str, dtype) -> pl.Expr:
    """Coerce a datetime column to canonical tz-naive ``Datetime('us')``."""
    if isinstance(dtype, pl.Datetime):
        expr = pl.col(column)
        if dtype.time_zone is not None:
            expr = expr.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        return expr.cast(pl.Datetime("us")).alias(column)
    # Fallback for backends that surface datetimes as text.
    return (
        pl.col(column)
        .cast(pl.Utf8, strict=False)
        .str.strptime(pl.Datetime("us"), strict=False)
        .alias(column)
    )


def _json_expr(column: str, json_column_mode: str, engine: str) -> pl.Expr:
    """Return a JSON column as raw text or parsed ``pl.Object`` per the mode."""
    mode = json_column_mode
    if mode == "auto":
        # The iterator engine already yields parsed Python objects; the
        # fast paths surface raw JSON text.
        mode = "object" if engine == "iterator" else "text"

    if mode == "text":
        return pl.col(column).cast(pl.Utf8, strict=False).alias(column)

    def _parse(value):
        if value is None:
            return None
        try:
            return orjson.loads(value) if isinstance(value, (str, bytes)) else value
        except Exception:
            return value

    return pl.col(column).map_elements(_parse, return_dtype=pl.Object).alias(column)

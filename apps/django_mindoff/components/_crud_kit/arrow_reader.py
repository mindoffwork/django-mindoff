"""
Read fast path: build Polars frames straight from the database instead of
materializing one Python ``dict`` per row through the ORM iterator.

A read always takes the fastest path that can still return correct data:

- ConnectorX's zero-copy DB -> Arrow transfer (``pl.read_database_uri``) when
  the read is safe — i.e. outside an open transaction, since ConnectorX opens
  its own connection and cannot see uncommitted rows (nor reach an in-memory
  SQLite database).
- Otherwise (open transaction, in-memory SQLite, ConnectorX unavailable) the
  queryset's compiled SQL runs through Django's own cursor, which shares the
  live connection — handling placeholder conversion, parameter adaptation and
  transaction visibility on every backend — and the frame is assembled
  column-wise: Arrow-native when the driver cursor exposes ``fetch_arrow_table``,
  otherwise from row tuples (never list-of-dicts). Streaming reads always use
  this cursor path, since ConnectorX has no streaming cursor.

Frames are normalized to the canonical dtypes in
:data:`dtypes.DJANGO_TO_POLARS_TYPE_MAP` so reads stay consistent with what the
create/update validators expect.
"""

import atexit
import datetime
import math
import os
import pathlib
import tempfile
import weakref
from decimal import Decimal
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
def read_frame(qs, *, json_column_mode: str = "auto"):
    """Return an eager ``pl.DataFrame`` for ``qs`` via the fastest correct path.

    ``qs`` must already be a ``.values()`` queryset and is expected to be sliced
    (LIMIT/OFFSET applied) by the caller for pagination.

    Uses ConnectorX's zero-copy DB->Arrow transfer when the read is safe — i.e.
    outside an open transaction, since ConnectorX opens its own connection and
    cannot see uncommitted rows — and otherwise (open transaction, in-memory
    SQLite, ConnectorX unavailable) reads through Django's own cursor, which
    shares the live connection.
    """
    model = qs.model
    frm = _try_connectorx(qs) if _connectorx_usable(qs.db) else None
    if frm is None:
        frm = _execute_django(qs)
    return _normalize(frm, model, json_column_mode)


def _connectorx_usable(db_alias: str) -> bool:
    """Whether ConnectorX may be used for ``db_alias`` right now.

    ConnectorX reads over its own connection, so it cannot see rows written but
    not yet committed in the current transaction. We therefore only reach for it
    outside an open atomic block; inside one (Django's ``TestCase``,
    ``ATOMIC_REQUESTS``, an explicit ``transaction.atomic()``) the read defers to
    the Django cursor, which shares the live connection.
    """
    return not connections[db_alias].in_atomic_block


def stream_batches(qs, *, batch_size: int, json_column_mode: str = "auto"):
    """Yield the queryset as a sequence of Polars frames, never concatenated.

    Memory-bounded: rows are pulled from Django's cursor one ``batch_size`` chunk
    at a time via ``fetchmany`` and each batch is normalized to the canonical
    model dtypes. (Streaming always uses the cursor — ConnectorX has no streaming
    cursor.)
    """
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
            yield _normalize(frm, model, json_column_mode)


def scan_to_lazy(qs, *, batch_size: int,
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
            qs, batch_size=batch_size, json_column_mode=json_column_mode,
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


def _try_connectorx(qs):
    """Read via ConnectorX, or return ``None`` to signal a (silent) fallback.

    Returns ``None`` — so the caller transparently uses Django's cursor — when
    ConnectorX is not importable, has no usable URI for the backend (unsupported
    engine or in-memory SQLite), the query parameters cannot be safely inlined,
    or the transfer itself fails.
    """
    try:
        import connectorx  # noqa: F401
    except Exception:
        return None

    uri = _connectorx_uri(qs.db)
    if uri is None:
        return None

    # ConnectorX uses its own connection and accepts only a raw SQL string (no
    # bound parameters). Compile the *parameterized* query and inline the
    # parameters as safely-escaped SQL literals. We must NOT use str(qs.query):
    # it renders string/LIKE parameters unquoted, producing SQL that is both
    # invalid and injectable. The compiled SQL aliases each column to its
    # `.values()` key, so result column names already match.
    engine = settings.DATABASES.get(qs.db, {}).get("ENGINE", "")
    sql, params = qs.query.get_compiler(using=qs.db).as_sql()
    try:
        inlined_sql = _inline_params(sql, params, mysql="mysql" in engine)
    except ValueError:
        return None
    try:  # pragma: no cover - needs live DB
        return pl.read_database_uri(inlined_sql, uri)
    except Exception:  # pragma: no cover - needs live DB
        return None


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


def _inline_params(sql: str, params, *, mysql: bool) -> str:
    """Inline parameters into format-paramstyle (``%s``) SQL as safe literals.

    Django compiles querysets with ``%s`` placeholders and ``%%`` for literal
    percent signs (the DB-API "format" paramstyle). ConnectorX accepts only a raw
    SQL string, so each parameter is rendered as a properly-escaped SQL literal
    and substituted with Python's ``%`` operator — which expands ``%s`` from the
    literals and collapses ``%%`` to ``%`` exactly as the driver would. Crucially,
    ``%`` characters *inside* the substituted literals (e.g. a ``LIKE`` pattern)
    are not re-interpreted, so escaping is single-pass and injection-safe.
    """
    literals = tuple(_sql_literal(p, mysql=mysql) for p in params)
    return sql % literals


def _sql_literal(value, *, mysql: bool) -> str:
    """Render a Python value as a safely-escaped SQL literal.

    Every string (and any unrecognized type, via ``str()``) is wrapped in single
    quotes with embedded quotes doubled (and backslashes escaped on MySQL), so a
    value can never break out of its literal — this is the SQL-injection guard.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cannot inline non-finite float")
        return repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("cannot inline non-finite Decimal")
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Hex blob literal (X'..'): only hex digits, so inherently injection-safe.
        return "X'" + bytes(value).hex() + "'"
    return _quote_string(_stringify(value), mysql=mysql)


def _stringify(value) -> str:
    """Render non-numeric values to their SQL string-literal text form."""
    if isinstance(value, datetime.datetime):
        # Space separator matches Django's stored SQLite datetime text.
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return value if isinstance(value, str) else str(value)


def _quote_string(text: str, *, mysql: bool) -> str:
    """Quote a string as a SQL literal, escaping for injection safety.

    Standard SQL (SQLite, PostgreSQL with ``standard_conforming_strings`` on —
    the default) treats backslashes literally, so doubling single quotes is
    sufficient. MySQL processes backslash escapes by default, so backslashes are
    doubled as well. A NUL byte cannot appear in a SQL string literal and is
    rejected outright.
    """
    if "\x00" in text:
        raise ValueError("NUL byte is not allowed in a SQL string literal")
    if mysql:
        text = text.replace("\\", "\\\\")
    return "'" + text.replace("'", "''") + "'"


def _normalize(
    frm: pl.DataFrame, model, json_column_mode: str
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
            exprs.append(_json_expr(column, json_column_mode))
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


def _json_expr(column: str, json_column_mode: str) -> pl.Expr:
    """Return a JSON column as raw text or parsed ``pl.Object`` per the mode."""
    mode = json_column_mode
    if mode == "auto":
        # The DB surfaces JSON as raw text; ``object`` is an explicit opt-in.
        mode = "text"

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

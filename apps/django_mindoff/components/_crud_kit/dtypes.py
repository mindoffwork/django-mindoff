"""
Canonical Django-field -> Polars-dtype mapping.

Single source of truth shared by the CRUD write path (``RowValidator``'s
type-consistency gate) and the read fast path (``mo_crud_kit.read``). Keeping
one table here guarantees that frames produced by reads carry exactly the
dtypes the create/update validators expect, so a read -> update round-trip
never trips the column-type-mismatch check.
"""

import polars as pl


# ----------------
# Constants
# ----------------
DJANGO_TO_POLARS_TYPE_MAP = {
    "AutoField": pl.Int64,
    "BigAutoField": pl.Int64,
    "SmallAutoField": pl.Int16,
    "IntegerField": pl.Int32,
    "BigIntegerField": pl.Int64,
    "SmallIntegerField": pl.Int16,
    "PositiveIntegerField": pl.UInt32,
    "PositiveSmallIntegerField": pl.UInt16,
    "FloatField": pl.Float64,
    "DecimalField": pl.Decimal,
    "BooleanField": pl.Boolean,
    "CharField": pl.Utf8,
    "TextField": pl.Utf8,
    "SlugField": pl.Utf8,
    "EmailField": pl.Utf8,
    "URLField": pl.Utf8,
    "UUIDField": pl.Utf8,
    "GenericIPAddressField": pl.Utf8,
    "BinaryField": pl.Binary,
    "FileField": pl.Utf8,
    "ImageField": pl.Utf8,
    "DateField": pl.Date,
    "DateTimeField": pl.Datetime("us"),
    "TimeField": pl.Time,
    "DurationField": pl.Duration("us"),
    # JSON is carried as compact JSON text (Utf8) so it serializes cleanly to
    # the database on write and round-trips through read/update unchanged.
    "JSONField": pl.Utf8,
    "ForeignKey": pl.Utf8,
    "OneToOneField": pl.Utf8,
    "ManyToManyField": pl.List(pl.Utf8),
}


# ----------------
# Functions
# ----------------
def resolve_polars_dtype(field):
    """Return the canonical Polars dtype for a concrete Django field.

    Mirrors the resolution used by ``RowValidator``'s type-consistency gate:
    looks up by field class name and refines ``DecimalField`` with its
    ``max_digits``/``decimal_places``. Returns ``None`` when the field type has
    no mapping (caller should then leave the column's inferred dtype untouched).
    """
    django_field_name = field.__class__.__name__
    dtype = DJANGO_TO_POLARS_TYPE_MAP.get(django_field_name)
    if dtype is None:
        return None
    if django_field_name == "DecimalField":
        precision = getattr(field, "max_digits", None)
        scale = getattr(field, "decimal_places", None)
        if precision is not None and scale is not None:
            dtype = pl.Decimal(precision=precision, scale=scale)
    return dtype

import datetime
import warnings
from typing import Dict, Type, Union

import orjson
import polars as pl
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

from ..polars_kit import mo_polars_kit
from django.conf import settings

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
    "JSONField": pl.Object,
    "ForeignKey": pl.Utf8,
    "OneToOneField": pl.Utf8,
    "ManyToManyField": pl.List(pl.Utf8),
}
ERROR_COL = getattr(settings, "POLARS_VALIDATOR_ERROR_COL", None) or "__error__info"


# ----------------
# Classes
# ----------------
class RowValidator:
    def __init__(
        self,
        df_dict: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        is_remove_html_tags: bool = False,
        is_normalize_text: bool = True,
    ):
        self.df_dict = df_dict
        self.is_remove_html_tags = is_remove_html_tags
        self.is_normalize_text = is_normalize_text

    def run(self) -> Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]]:
        return {
            model: self._sanitize_model_frm(model, df)
            for model, df in self.df_dict.items()
        }

    def _sanitize_model_frm(self, model, df):
        if mo_polars_kit.is_frm_empty(df):
            return df
        if ERROR_COL not in df.columns:
            df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(ERROR_COL))
        df_schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        for field in model._meta.concrete_fields:
            if (
                not isinstance(field, models.Field)
                or (field.db_column or field.name) not in df_schema
            ):
                continue
            df = self._sanitize_field(model, df, field)
        return df

    def _sanitize_field(self, model, df, field):
        name = field.db_column or field.name
        dtype = df.collect_schema().get(name)
        if isinstance(field, models.ManyToManyField):
            raise ValueError(
                f"[{model.__name__}.{name}] ManyToManyField is not supported Yet. "
                f"Use Foreignkey or OneToOneField."
            )
        elif isinstance(field, models.BinaryField):
            raise ValueError(
                f"[{model.__name__}.{name}] BinaryField is not supported. "
                f"To store base64, use a TextField. For files, use the centralized file uploader."
            )
        if field.choices:
            df = self._apply_choices(model, df, field, dtype)
        if isinstance(field, (models.EmailField, models.URLField)):
            df = self._sanitize_email_url_field(model, df, field, dtype)
        elif isinstance(field, models.SlugField):
            df = self._sanitize_slug_field(model, df, field, dtype)
        elif isinstance(field, (models.CharField, models.TextField)):
            df = self._sanitize_text_field(model, df, field, dtype)
        elif isinstance(
            field,
            (
                models.IntegerField,
                models.BigIntegerField,
                models.SmallIntegerField,
                models.PositiveIntegerField,
                models.PositiveSmallIntegerField,
            ),
        ):
            df = self._sanitize_integer_field(model, df, field, dtype)
        elif isinstance(field, (models.DecimalField, models.FloatField)):
            df = self._sanitize_float_decimal_field(model, df, field, dtype)
        elif isinstance(field, models.BooleanField):
            df = self._sanitize_boolean_field(model, df, field, dtype)
        elif isinstance(
            field, (models.DateTimeField, models.DateField, models.TimeField)
        ):
            df = self._sanitize_date_field(model, df, field, dtype)
        elif isinstance(field, models.DurationField):
            df = self._sanitize_duration_field(model, df, field, dtype)
        elif isinstance(
            field, (models.UUIDField, models.ForeignKey, models.OneToOneField)
        ):
            df = self._sanitize_uuid_field(model, df, field, dtype)
        elif isinstance(field, models.GenericIPAddressField):
            df = self._sanitize_ip_field(model, df, field, dtype)
        elif isinstance(field, models.JSONField):
            df = self._sanitize_json_field(model, df, field, dtype)
        return df

    def _apply_choices(self, model, df, field, dtype):
        choices = field.choices()() if callable(field.choices) else field.choices or []
        if not all(isinstance(c, (list, tuple)) and len(c) == 2 for c in choices):
            raise ValueError(
                f"{model.__name__}.{field.name} choices must be (short, long) pairs"
            )
        is_blank_allowed = getattr(field, "blank", False)

        def transform(col: pl.Expr) -> pl.Expr:
            col_utf8 = col.cast(pl.Utf8).str.strip_chars()
            blank_expr = pl.when((col_utf8 == "") & pl.lit(is_blank_allowed)).then(
                pl.lit("")
            )
            choice_exprs = [
                pl.when((col_utf8 == str(short)) | (col_utf8 == str(long))).then(
                    pl.lit(str(short))
                )
                for short, long in choices
            ]
            return pl.coalesce(
                ([blank_expr] if is_blank_allowed else [])
                + choice_exprs
                + [pl.lit(None)]
            )

        return self._transform_and_validate_column(
            model,
            df,
            field,
            dtype,
            transform_fn=transform,
            label="Choice",
            is_choices=True,
        )

    def _sanitize_text_field(self, model, df, field, dtype):
        df = self._transform_and_validate_column(
            model,
            df,
            field,
            dtype,
            transform_fn=lambda col: col.cast(pl.Utf8).str.strip_chars(),
            label="Text",
        )
        return df

    def _sanitize_slug_field(self, model, df, field, dtype):
        df = self._transform_and_validate_column(
            model,
            df,
            field,
            dtype,
            transform_fn=lambda col: (
                col.cast(pl.Utf8)
                .str.strip_chars()
                .str.normalize("NFKD")
                .str.replace_all(r"\p{Mn}+", "")
                .str.to_lowercase()
                .str.replace_all(r"[^\w\s-]", "")
                .str.replace_all(r"[-\s]+", "-")
                .str.strip_chars("-")
            ),
            label="Slug",
        )
        return df

    def _sanitize_email_url_field(self, model, df, field, dtype):
        is_url = isinstance(field, models.URLField)

        def transform(col: pl.Series) -> pl.Series:
            if dtype not in (pl.Utf8, pl.Object):
                return col
            is_blank = col.is_null() | (
                col.cast(pl.Utf8).str.strip_chars().str.len_chars() == 0
            )
            col_cleaned = (
                col.cast(pl.Utf8).str.strip_chars().str.replace_all(r"\s+", "")
            )
            if not is_url:
                transformed = col_cleaned.str.to_lowercase()
            else:
                scheme = (
                    col_cleaned.str.extract(r"(?i)^(\w+)://", 1)
                    .str.to_lowercase()
                    .fill_null("")
                )
                domain = (
                    col_cleaned.str.extract(r"^(?:\w+://)?([^/]+)", 1)
                    .str.to_lowercase()
                    .fill_null("")
                )
                path = col_cleaned.str.extract(
                    r"^(?:\w+://)?[^/]+(/[^/][^?#]*)", 1
                ).fill_null("")
                valid_domain = domain.str.len_chars() > 0
                has_scheme = scheme.str.len_chars() > 0
                transformed = (
                    pl.when(valid_domain)
                    .then(
                        pl.when(has_scheme)
                        .then(pl.format("{}://{}{}", scheme, domain, path))
                        .otherwise(pl.format("{}{}", domain, path))
                    )
                    .otherwise(pl.lit(None))
                )
            col = pl.when(is_blank).then(col).otherwise(transformed)
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Email_URL"
        )
        return df

    def _sanitize_integer_field(self, model, df, field, dtype):
        field_config = {
            models.IntegerField: (-2_147_483_648, 2_147_483_647, pl.Int32),
            models.BigIntegerField: (
                -9_223_372_036_854_775_808,
                9_223_372_036_854_775_807,
                pl.Int64,
            ),
            models.SmallIntegerField: (-32_768, 32_767, pl.Int16),
            models.PositiveIntegerField: (0, 4_294_967_295, pl.UInt32),
            models.PositiveSmallIntegerField: (0, 32_767, pl.UInt16),
        }
        min_val, max_val, pl_dtype = field_config[type(field)]

        def transform(col: pl.Series) -> pl.Series:
            col = col.cast(pl.Utf8).str.strip_chars()
            sign = col.str.extract(r"^([+-])", 1).fill_null("")
            digits = col.str.replace_all(r"[^\d]", "")
            numeric_str = sign + digits
            numeric = numeric_str.cast(pl.Int64, strict=False)
            clamped = (
                pl.when(numeric < min_val)
                .then(min_val)
                .when(numeric > max_val)
                .then(max_val)
                .otherwise(numeric)
            )
            col = clamped.cast(pl_dtype, strict=False)
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Integer"
        )
        return df

    def _sanitize_float_decimal_field(self, model, df, field, dtype):
        is_decimal = isinstance(field, models.DecimalField)
        precision = getattr(field, "max_digits", None)
        scale = getattr(field, "decimal_places", None)

        def transform(col: pl.Series) -> pl.Series:
            col = col.cast(pl.Utf8).str.strip_chars()
            sign = col.str.extract(r"^([+-])", 1).fill_null("")
            body = col.str.replace_all(r"[^\d\.eE]", "")
            cleaned = sign + body
            if is_decimal:
                col = cleaned.cast(
                    pl.Decimal(precision=precision, scale=scale), strict=False
                )
            else:
                col = cleaned.cast(pl.Float64, strict=False)
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Float_Decimal"
        )
        return df

    def _sanitize_boolean_field(self, model, df, field, dtype):
        def transform(col: pl.Series) -> pl.Series:
            if dtype not in (pl.Utf8, pl.Object):
                return col
            col = col.cast(pl.Utf8).str.strip_chars().str.to_lowercase()
            col = (
                pl.when((col == "true") | (col == "1"))
                .then(True)
                .when((col == "false") | (col == "0"))
                .then(False)
                .otherwise(None)
            )
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Boolean"
        )
        return df

    def _sanitize_date_field(self, model, df, field, dtype):
        name = field.db_column or field.name
        now = timezone.now()
        today = timezone.localdate()
        is_datetime = isinstance(field, models.DateTimeField)
        is_time = isinstance(field, models.TimeField)
        is_date = isinstance(field, models.DateField)

        if getattr(field, "auto_now", False):
            df = df.with_columns(pl.lit(now if is_datetime else today).alias(name))
        elif getattr(field, "auto_now_add", False):
            df = df.with_columns(
                pl.when(pl.col(name).is_null())
                .then(pl.lit(now if is_datetime else today))
                .otherwise(pl.col(name))
                .alias(name)
            )

        def transform(col: pl.Series) -> pl.Series:
            if dtype not in (pl.Utf8, pl.Object):
                return col
            col = col.cast(pl.Utf8).str.strip_chars()
            offset_re = r"([+-])(\d{2}):(\d{2})$"
            has_offset = col.str.extract(offset_re, 1).is_not_null()
            compressed_offset = (
                col.str.extract(offset_re, 1)
                + col.str.extract(offset_re, 2)
                + col.str.extract(offset_re, 3)
            ).fill_null("")
            col_no_offset = col.str.replace_all(offset_re, "")
            col = (
                pl.when(has_offset)
                .then(col_no_offset + compressed_offset)
                .otherwise(col)
            )
            col = col.str.replace_all("T", " ").str.to_lowercase()

            has_date = col.str.contains(r"\d{4}-\d{2}-\d{2}")
            has_time = col.str.contains(r"\d{2}:\d{2}(:\d{2})?")
            dummy_date = "1970-01-01"
            dummy_time = "00:00:00"

            datetime_format_tz = "%Y-%m-%d %H:%M:%S%z"
            datetime_format = "%Y-%m-%d %H:%M:%S"
            if is_datetime:
                col = (
                    col.str.strptime(pl.Datetime, datetime_format_tz, strict=False)
                    .dt.convert_time_zone("UTC")
                    .fill_null(
                        col.str.strptime(
                            pl.Datetime, datetime_format, strict=False
                        ).dt.replace_time_zone("UTC")
                    )
                    .cast(pl.Datetime)
                )
            elif is_time:
                col = (
                    pl.when(~has_date & has_time)
                    .then(dummy_date + " " + col)
                    .otherwise(col)
                )
                col = (
                    col.str.strptime(pl.Datetime, datetime_format_tz, strict=False)
                    .dt.convert_time_zone("UTC")
                    .fill_null(
                        col.str.strptime(
                            pl.Datetime, datetime_format, strict=False
                        ).dt.replace_time_zone("UTC")
                    )
                    .dt.time()
                )
            elif is_date:
                col = (
                    pl.when(has_date & ~has_time)
                    .then(col + " " + dummy_time)
                    .otherwise(col)
                )
                col = (
                    col.str.strptime(pl.Datetime, datetime_format_tz, strict=False)
                    .dt.convert_time_zone("UTC")
                    .fill_null(
                        col.str.strptime(
                            pl.Datetime, datetime_format, strict=False
                        ).dt.replace_time_zone("UTC")
                    )
                    .dt.date()
                )
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Date_Time"
        )
        return df

    def _sanitize_duration_field(self, model, df, field, dtype):
        def transform(col: pl.Series) -> pl.Series:
            if dtype == pl.Duration("us"):
                return col
            elif isinstance(dtype, pl.Duration):
                factor = {
                    "ns": 1_000,
                    "ms": 1_000,
                    "s": 1_000_000,
                    "m": 60_000_000,
                    "h": 3_600_000_000,
                    "d": 86_400_000_000,
                }.get(dtype.time_unit, None)
                if factor is None:
                    raise ValueError(f"Unsupported duration unit: {col.dtype}")
                col = (col.cast(pl.Int64) * factor).cast(pl.Duration("us"))
            else:
                col = (
                    col.cast(pl.Utf8)
                    .str.strip_chars()
                    .str.extract(r"^([+-]?\d+)$", 1)
                    .cast(pl.Int64, strict=False)
                    .cast(pl.Duration("us"), strict=False)
                )
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="Duration"
        )
        return df

    def _sanitize_uuid_field(self, model, df, field, dtype):
        field_name = field.db_column or field.name
        if dtype not in (pl.Utf8, pl.String, str):
            try:
                df = df.with_columns(df[field_name].cast(pl.Utf8).alias(field_name))
            except Exception:
                warnings.warn(
                    f"Column `{field_name}` is mapped to a Django UUIDField but its dtype is not "
                    f"one of the expected types: (pl.Utf8, pl.String, str). "
                    f"Falling back to `map_elements` for conversion, which may hurt performance. "
                    f"Tip: Cast the column to `pl.Utf8` beforehand to avoid this warning.",
                    RuntimeWarning,
                )
                convert_to_str = lambda row: (str(row) if row is not None else None)
                df = mo_polars_kit.frm_fill_notnull(
                    df,
                    column=field_name,
                    fill_value=convert_to_str,
                    row_param="row",
                    mode="map",
                    dtype=pl.Utf8,
                )

        def transform(col: pl.Series) -> pl.Series:
            col = col.cast(pl.Utf8).str.strip_chars()
            uuid_regex = r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$"
            is_invalid = col.is_not_null() & ~col.str.contains(uuid_regex)
            col = pl.when(is_invalid).then(None).otherwise(col)
            col = col.str.to_lowercase().str.replace_all("-", "")
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="UUID"
        )
        return df

    def _sanitize_ip_field(self, model, df, field, dtype):
        protocol = getattr(field, "protocol", "both").lower()
        unpack_ipv4 = getattr(field, "unpack_ipv4", False)

        def transform(col: pl.series) -> pl.series:
            col = col.cast(pl.Utf8).str.strip_chars()
            if unpack_ipv4 and protocol in {"both", "ipv4"}:
                col = col.str.replace(r"^::ffff:", "")
            ipv4_pattern = r"^(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})(\.(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}$"
            ipv6_pattern = r"^([0-9a-fA-F]{1,4}:){1,7}[0-9a-fA-F]{1,4}$"  # simplified
            if protocol == "ipv4":
                accepted_pattern = col.str.contains(ipv4_pattern, literal=False)
            elif protocol == "ipv6":
                accepted_pattern = col.str.contains(ipv6_pattern, literal=False)
            else:
                accepted_pattern = col.str.contains(
                    ipv4_pattern, literal=False
                ) | col.str.contains(ipv6_pattern, literal=False)
            col = pl.when(accepted_pattern).then(col).otherwise(pl.lit(None))
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="IP_Address"
        )
        return df

    def _sanitize_json_field(self, model, df, field, dtype):
        def transform(col: pl.Series) -> pl.Series:
            def try_parse(val):
                if val is None:
                    return None
                try:
                    if isinstance(val, str):
                        val = orjson.loads(val)
                    if not isinstance(val, list):
                        val = [val]
                    orjson.dumps(val)
                    return val
                except Exception:
                    return None

            col = col.map_elements(try_parse, return_dtype=pl.Object)
            return col

        df = self._transform_and_validate_column(
            model, df, field, dtype, transform_fn=transform, label="JSON"
        )
        return df

    def _transform_and_validate_column(
        self,
        model,
        df,
        field,
        dtype,
        transform_fn,
        label: str,
        is_choices: bool = False,
    ):
        name = field.db_column or field.name
        is_blank_true = getattr(field, "blank", False)
        orig_null_col = f"__orig_null__{name}"
        post_null_col = f"__post_null__{name}"
        invalid_col = f"__invalid__{name}"

        try:
            django_field_name = field.__class__.__name__
            expected_dtype = DJANGO_TO_POLARS_TYPE_MAP.get(django_field_name)
            if expected_dtype is None:
                raise ValueError(
                    f"Django field not Supported with 'django-mindoff' Package: {django_field_name}"
                )
            if django_field_name == "DecimalField":
                precision = getattr(field, "max_digits", None)
                scale = getattr(field, "decimal_places", None)
                if precision is not None and scale is not None:
                    expected_dtype = pl.Decimal(precision=precision, scale=scale)
            valid_string_dtypes = (pl.Utf8, str)
            if not is_blank_true and dtype in valid_string_dtypes:
                df = df.with_columns(
                    pl.when(
                        pl.col(name).cast(pl.Utf8).str.strip_chars().str.len_chars()
                        == 0
                    )
                    .then(None)
                    .otherwise(pl.col(name))
                    .alias(name)
                )
            df = df.with_columns(pl.col(name).is_null().alias(orig_null_col))
            df = self.__apply_default_to_df_column(df, name, field, django_field_name)
            transformed = transform_fn(pl.col(name))
            if not is_choices:
                try:
                    transformed = transformed.cast(expected_dtype)
                except Exception as e:
                    raise ValueError(
                        f"[{model.__name__}.{name}] Conversion to {expected_dtype} after transformation failed: {e}"
                    )
            df = df.with_columns(transformed.is_null().alias(post_null_col))
            df = df.with_columns(
                pl.when(pl.col(post_null_col) & ~pl.col(orig_null_col))
                .then(True)
                .otherwise(False)
                .alias(invalid_col)
            )
            df = df.with_columns(transformed.alias(name))
            df = df.with_columns(
                pl.when(pl.col(invalid_col))
                .then(
                    self._append_or_initialize_error(
                        error_key="invalid_value",
                        context=f"{model.__name__}.{name}",
                        exception="",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )
            if not is_choices:
                df = self._field_vs_data_validation(
                    model, df, field, name, expected_dtype
                )
            return df
        except Exception as e:
            raise ValueError(
                f"[{model.__name__}.{name}] {label} Validation failed: {e}"
            )

    def __apply_default_to_df_column(self, df, field_name, field, django_field_name):
        if not field.has_default():
            return df
        default = field.default
        is_timedelta = isinstance(field.get_default(), datetime.timedelta)
        is_uuid = django_field_name == "UUIDField"

        def __apply_default():
            value = default() if callable(default) else default
            if is_timedelta:
                value = int(value.total_seconds() * 1_000_000)
            if is_uuid:
                value = str(value)
            return value

        mode = "sink_map" if callable(default) else "lit"
        return mo_polars_kit.frm_fill_null(
            df, column=field_name, fill_value=__apply_default, mode=mode
        )

    def _field_vs_data_validation(self, model, df, field, name, expected_dtype):
        is_null_true = getattr(field, "null", False)
        is_blank_true = getattr(field, "blank", False)
        dtype = df.collect_schema().get(name)
        # 1. Required field validation
        if not is_null_true and not is_blank_true:
            if isinstance(
                field, (models.ForeignKey, models.OneToOneField)
            ) and mo_polars_kit.has_nulls_in_frm_col(df, name):
                raise ValueError(
                    self._response_messages(
                        error_key="missing_required_value",
                        context=f"{model.__name__}.{name}",
                        exception="Foreign key value is required and cannot be null.",
                    )
                )
            df = df.with_columns(
                pl.when(pl.col(name).is_null())
                .then(
                    self._append_or_initialize_error(
                        error_key="missing_required_value",
                        context=f"{model.__name__}.{name}",
                        exception=f"field={name}",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )

        # 2. db_column mismatch
        db_column = getattr(field, "db_column", None)
        if db_column and db_column != name:
            df = df.with_columns(
                pl.when(pl.lit(True))
                .then(
                    self._append_or_initialize_error(
                        error_key="column_name_mismatch",
                        context=f"{model.__name__}.{name}",
                        exception=f"expected={db_column}, actual={name} ",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )

        # 3. Max length / digits
        max_length = getattr(field, "max_length", None)
        max_digits = getattr(field, "max_digits", None)
        if max_length:
            length_check_col = f"__length_check__{name}"
            df = df.with_columns(
                pl.col(name).cast(pl.Utf8).str.len_chars().alias(length_check_col)
            )
            df = df.with_columns(
                pl.when(pl.col(length_check_col) > max_length)
                .then(
                    self._append_or_initialize_error(
                        error_key="max_length",
                        context=f"{model.__name__}.{name}",
                        exception=f"max_length={max_length}",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )
        if max_digits:
            digit_check_col = f"__digit_check__{name}"
            df = df.with_columns(
                pl.col(name)
                .cast(pl.Utf8)
                .str.replace_all(r"[^\d]", "")
                .alias(digit_check_col)
            )
            df = df.with_columns(
                pl.when(pl.col(digit_check_col).str.len_chars() > max_digits)
                .then(
                    self._append_or_initialize_error(
                        error_key="max_digits",
                        context=f"{model.__name__}.{name}",
                        exception=f"max_digits={max_digits}",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )

        # 4. Min/Max value validation
        min_value, max_value = self.__get_min_max_from_validators(field)
        if min_value is not None:
            min_check_col = f"__min_check__{name}"
            df = df.with_columns((pl.col(name) < min_value).alias(min_check_col))
            df = df.with_columns(
                pl.when(pl.col(min_check_col))
                .then(
                    self._append_or_initialize_error(
                        error_key="min_value",
                        context=f"{model.__name__}.{name}",
                        exception=f"min_value={min_value}",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )
        if max_value is not None:
            max_check_col = f"__max_check__{name}"
            df = df.with_columns((pl.col(name) > max_value).alias(max_check_col))
            df = df.with_columns(
                pl.when(pl.col(max_check_col))
                .then(
                    self._append_or_initialize_error(
                        error_key="max_value",
                        context=f"{model.__name__}.{name}",
                        exception=f"max_value={max_value}",
                    )
                )
                .otherwise(pl.col(ERROR_COL))
                .alias(ERROR_COL)
            )

        # 5. Type consistency check
        if dtype != expected_dtype:
            raise ValueError(
                self._response_messages(
                    error_key="column_type_mismatch",
                    context=f"{model.__name__}.{name}",
                    exception=f"expected={expected_dtype}, actual={dtype}",
                )
            )

        # 6. UUID primary key check
        if getattr(field, "primary_key", False) and mo_polars_kit.has_nulls_in_frm_col(
            df, name
        ):
            raise ValueError(
                self._response_messages(
                    error_key="missing_required_value",
                    context=f"{model.__name__}.{name}",
                    exception="Valid UUID is required for Primary key",
                )
            )

        temp_cols = [c for c in df.columns if c.startswith("__") and c != ERROR_COL]
        df = df.drop(temp_cols)
        return df

    def __get_min_max_from_validators(self, field):
        min_value = None
        max_value = None
        for validator in getattr(field, "validators", []):
            if isinstance(validator, MinValueValidator):
                if min_value is None or validator.limit_value > min_value:
                    min_value = validator.limit_value
            elif isinstance(validator, MaxValueValidator):
                if max_value is None or validator.limit_value < max_value:
                    max_value = validator.limit_value
        return min_value, max_value

    def _append_or_initialize_error(
        self, error_key: str, context: str = "", exception: str = ""
    ) -> pl.Expr:
        message = self._response_messages(
            error_key=error_key, context=context, exception=exception
        )
        return (
            pl.when(pl.col(ERROR_COL).is_not_null())
            .then(pl.col(ERROR_COL) + pl.lit(message))
            .otherwise(pl.lit(message))
        )

    def _response_messages(
        self, error_key: str, context: str = "", exception: str = ""
    ) -> str:
        messages = {
            "conversion": f"[{context}] Conversion to expected datatype after transformation failed: {exception}",
            "invalid_value": f"[{context}] Invalid value",
            "missing_required_value": f"[{context}] This field is required: {exception}",
            "column_name_mismatch": f"[{context}] Column name mismatch: {exception}",
            "max_length": f"[{context}] Exceeds Max Length: {exception}",
            "max_digits": f"[{context}] Exceeds Max Digits: {exception}",
            "max_value": f"[{context}] Exceeds Max Value: {exception}",
            "min_value": f"[{context}] Below Min Value: {exception}",
            "column_type_mismatch": f"[{context}] Column type mismatch: {exception}",
        }
        message = messages.get(error_key, "")
        output = message + " ; " if message else ""
        return output

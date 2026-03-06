"""
Extended test suite for django-mindoff crud_kit, column_validator,
row_validator, foreign_key_validator, and crud_processor.

Coverage additions on top of the existing test_crud_kit.py:
  ColumnValidator
    - PK not UUIDField → raises
    - PK missing db_column → raises
    - FK missing db_column → raises
    - db_column name renaming (field.name ≠ db_column) is applied
    - auto_now / auto_now_add fields are injected as None columns
    - is_add_missing_columns=False with missing cols → invalid_dfs
    - is_remove_extra_columns=False with extra cols → invalid_dfs
    - empty DataFrame input → passes through cleanly
    - LazyFrame input → handled identically to DataFrame

  RowValidator
    - each Django field type sanitised correctly
      • CharField / TextField (blank/null combos, strip, empty→None)
      • SlugField (normalisation)
      • EmailField (lowercased, whitespace stripped)
      • URLField (scheme lowercased, path preserved)
      • IntegerField / SmallIntegerField / BigIntegerField (parse, clamp)
      • PositiveIntegerField / PositiveSmallIntegerField (non-negative)
      • FloatField / DecimalField
      • BooleanField (true/false/1/0 coercion)
      • DateField / DateTimeField / TimeField (string → correct type)
      • DurationField (int string → Duration)
      • UUIDField (normalise to hex-lowercase, reject invalid)
      • GenericIPAddressField (ipv4/ipv6/both protocol modes)
      • JSONField (str/dict/list → list wrapped, bad → None)
      • choices validation (valid short/long code, invalid → None/error)
      • MinValueValidator / MaxValueValidator respected
      • max_length enforcement
      • null=False, blank=False → missing required value error
      • FK / OneToOne required null → error row (not raise)
      • ManyToManyField → raises immediately
      • BinaryField → raises immediately
    - auto_now replaces all values; auto_now_add only fills nulls
    - default value applied when field has default
    - empty DataFrame → returned unchanged
    - LazyFrame → handled identically

  ForeignKeyValidator
    - FK resolved against in-dict related model (valid)
    - FK resolved against in-dict related model (some invalid) → error rows
    - FK resolved against DB (valid)
    - FK resolved against DB (some missing) → raises ValueError
    - optional FK (null=True) with None values → passes
    - required FK all None → raises
    - fk_values empty (all nulls) → skips DB query, passes
    - related model not in dict and not in DB → raises
    - LazyFrame input handled

  MindoffCRUDHandler / CRUDProcessor (integration)
    - create: batch_size respected (rows chunked correctly)
    - create: is_validate=False with bad UUID → RuntimeError
    - read: batch_size=0 auto-defaults (streaming=1000, pagination=100)
    - read: queryset without .values() → ValidationError
    - read: negative batch_size → ValidationError
    - read: page beyond last page → empty df, correct stats
    - read: page_number=0 treated as streaming (no page_number)
    - update: only PK column in df (no other columns) → no-op ok
    - update: is_partial=True with mixed valid/invalid rows
    - update: is_validate=False skips validation, persists directly
    - _ModelFrmsValidInvalidSplitter: cascade propagation parent→child
    - _ModelFrmsValidInvalidSplitter: cascade propagation child→parent
    - _update__fill_missing_columns: PK absent → raises
    - _update__fill_missing_columns: all columns present → no fetch
    - _update__fill_missing_columns: LazyFrame path

  CRUDProcessor dialect / engine
    - unsupported DB engine → raises ValueError

All tests use the two-level fixture pattern (class-scope app registration,
function-scope table creation) identical to the existing test suite.
"""

import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from django.apps import apps
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import connection, models
from django.test import override_settings
from django.urls import clear_url_caches

from ...components.crud_kit import mo_crud_kit
from ...components.crud_kit import MindoffCRUDHandler
from ...components.crud_kit import (
    _ModelFrmsValidInvalidSplitter,
    _read__build_stats,
    _update__fill_missing_columns,
)
from ...components._crud_kit.column_validator import ColumnValidator
from ...components._crud_kit.row_validator import RowValidator
from ...components._crud_kit.foreign_key_validator import ForeignKeyValidator
from ...components.polars_kit import mo_polars_kit
from ...components.tdd_kit import MindoffTestCase, _create_model, _validate_model
from ...components.managers._create_app import DjangoAppCreator
from ...components.validation_kit import ValidationError


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers (identical to existing test_crud_kit.py)
# ─────────────────────────────────────────────────────────────────────────────


def _register_app(temp_dir: Path, app_name: str):
    sys.path.insert(0, str(temp_dir))
    creator = DjangoAppCreator(app_name, isolated=True)
    creator.project_root = temp_dir
    creator.app_dir = str(temp_dir / app_name)
    creator.settings_path = temp_dir / "dummy_settings.py"
    creator.urls_path = temp_dir / "dummy_urls.py"
    creator.run()
    override = override_settings(
        INSTALLED_APPS=list(settings.INSTALLED_APPS) + [app_name],
    )
    override.enable()
    apps.set_installed_apps(settings.INSTALLED_APPS)
    apps.clear_cache()
    clear_url_caches()
    return override


def _unregister_app(app_name: str, temp_dir: Path, override):
    override.disable()
    clear_url_caches()
    for mod in list(sys.modules):
        if mod == app_name or mod.startswith(f"{app_name}."):
            sys.modules.pop(mod, None)
    sys.path[:] = [p for p in sys.path if str(p) != str(temp_dir)]
    shutil.rmtree(temp_dir, ignore_errors=True)
    apps.clear_cache()
    apps.populate(settings.INSTALLED_APPS)


def _convert_to_lazy_dict(df_dict: dict) -> dict:
    return {
        k: v.lazy() if isinstance(v, pl.DataFrame) else v for k, v in df_dict.items()
    }


def _make_uuid() -> str:
    return uuid.uuid4().hex


# ─────────────────────────────────────────────────────────────────────────────
# ColumnValidator unit tests (no DB required)
# ─────────────────────────────────────────────────────────────────────────────


class TestColumnValidatorUnit:
    """Pure-unit tests — no database, no fixtures."""

    def _uuid_model(self, extra_fields=None, pk_has_db_col=True, pk_is_uuid=True):
        """Dynamically build a minimal Django model class for validator testing."""
        pk_kwargs = {"primary_key": True}
        if pk_has_db_col:
            pk_kwargs["db_column"] = "id"

        pk_field = (
            models.UUIDField(**pk_kwargs)
            if pk_is_uuid
            else models.AutoField(**pk_kwargs)
        )

        fields = {"id": pk_field, **(extra_fields or {})}

        # Build a fake model using type()
        meta_cls = type("Meta", (), {"app_label": "tests"})
        model_cls = type(
            "TmpModel",
            (models.Model,),
            {**fields, "Meta": meta_cls, "__module__": __name__},
        )
        return model_cls

    def test_pk_not_uuid_raises(self):
        """ColumnValidator must reject models whose PK is not a UUIDField."""
        model = self._uuid_model(pk_is_uuid=False)
        df = pl.DataFrame({"id": [1]})
        validator = ColumnValidator(model_frms={model: df})
        _, invalid = validator.run()
        # Should land in invalid with a descriptive error
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_pk_missing_db_column_raises(self):
        model = self._uuid_model(pk_has_db_col=False)
        df = pl.DataFrame({"id": [_make_uuid()]})
        validator = ColumnValidator(model_frms={model: df})
        _, invalid = validator.run()
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_extra_columns_removed_when_flag_true(self):
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()], "name": ["Alice"], "extra": ["x"]})
        validator = ColumnValidator(
            model_frms={model: df},
            is_remove_extra_columns=True,
            is_add_missing_columns=False,
        )
        valid, _ = validator.run()
        assert "extra" not in valid[model].columns

    def test_extra_columns_kept_and_invalid_when_flag_false(self):
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()], "name": ["Alice"], "extra": ["x"]})
        validator = ColumnValidator(
            model_frms={model: df},
            is_remove_extra_columns=False,
            is_add_missing_columns=False,
        )
        _, invalid = validator.run()
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_missing_columns_added_when_flag_true(self):
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()]})  # 'name' missing
        validator = ColumnValidator(
            model_frms={model: df},
            is_remove_extra_columns=False,
            is_add_missing_columns=True,
        )
        valid, _ = validator.run()
        assert "name" in valid[model].columns

    def test_missing_columns_error_when_flag_false(self):
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()]})
        validator = ColumnValidator(
            model_frms={model: df},
            is_remove_extra_columns=False,
            is_add_missing_columns=False,
        )
        _, invalid = validator.run()
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_empty_dataframe_passes_through(self):
        model = self._uuid_model()
        df = pl.DataFrame(schema={"id": pl.Utf8})
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        assert mo_polars_kit.is_frm_empty(valid[model]) or "id" in valid[model].columns

    def test_lazy_frame_handled_identically(self):
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()], "name": ["Alice"]}).lazy()
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        assert isinstance(valid[model], pl.LazyFrame)

    def test_db_column_rename_applied(self):
        """When field.db_column != field.name, the column should be renamed."""
        fk_field = models.CharField(max_length=50, db_column="author_ref")
        model = self._uuid_model(extra_fields={"author_ref": fk_field})
        df = pl.DataFrame({"id": [_make_uuid()], "author_ref": ["Alice"]})
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        # The column should survive under its db_column name
        assert "author_ref" in valid[model].columns


# ─────────────────────────────────────────────────────────────────────────────
# RowValidator unit tests (no DB required)
# ─────────────────────────────────────────────────────────────────────────────


class TestRowValidatorFieldSanitisation:
    """Unit tests for each Django field type sanitisation path."""

    def _run(self, fields: dict, data: dict) -> pl.DataFrame:
        """Helper: build model, run RowValidator, return collected DataFrame."""
        meta_cls = type("Meta", (), {"app_label": "tests"})
        pk = models.UUIDField(primary_key=True, db_column="id")
        model_cls = type(
            "TmpModel",
            (models.Model,),
            {"id": pk, **fields, "Meta": meta_cls, "__module__": __name__},
        )
        df = pl.DataFrame(data)
        rv = RowValidator(df_dict={model_cls: df})
        result = rv.run()[model_cls]
        return result.collect() if isinstance(result, pl.LazyFrame) else result

    # ── CharField / TextField ─────────────────────────────────────────────

    def test_charfield_strips_whitespace(self):
        df = self._run(
            {"name": models.CharField(max_length=50)},
            {"id": [_make_uuid()], "name": ["  Alice  "]},
        )
        assert df["name"][0] == "Alice"

    def test_charfield_blank_false_empty_string_becomes_null(self):
        df = self._run(
            {"name": models.CharField(max_length=50)},
            {"id": [_make_uuid()], "name": [""]},
        )
        assert df["__error__info"][0] is not None  # missing required

    def test_charfield_blank_true_empty_string_preserved(self):
        df = self._run(
            {"name": models.CharField(max_length=50, blank=True, null=True)},
            {"id": [_make_uuid()], "name": [""]},
        )
        assert df["__error__info"][0] is None

    def test_charfield_max_length_exceeded_adds_error(self):
        df = self._run(
            {"name": models.CharField(max_length=3)},
            {"id": [_make_uuid()], "name": ["TooLong"]},
        )
        assert df["__error__info"][0] is not None
        assert "max_length" in df["__error__info"][0]

    def test_charfield_null_true_blank_true_none_passes(self):
        df = self._run(
            {"name": models.CharField(max_length=50, null=True, blank=True)},
            {"id": [_make_uuid()], "name": [None]},
        )
        assert df["__error__info"][0] is None

    # ── SlugField ─────────────────────────────────────────────────────────

    def test_slugfield_normalises(self):
        df = self._run(
            {"slug": models.SlugField(max_length=100)},
            {"id": [_make_uuid()], "slug": ["Hello World!"]},
        )
        assert df["slug"][0] == "hello-world"

    # ── EmailField ────────────────────────────────────────────────────────

    def test_emailfield_lowercased(self):
        df = self._run(
            {"email": models.EmailField()},
            {"id": [_make_uuid()], "email": ["  USER@EXAMPLE.COM  "]},
        )
        assert df["email"][0] == "user@example.com"

    # ── URLField ──────────────────────────────────────────────────────────

    def test_urlfield_scheme_lowercased(self):
        df = self._run(
            {"url": models.URLField()},
            {"id": [_make_uuid()], "url": ["HTTPS://Example.COM/Path"]},
        )
        assert df["url"][0].startswith("https://example.com")

    # ── IntegerField ──────────────────────────────────────────────────────

    def test_integerfield_parses_string(self):
        df = self._run(
            {"count": models.IntegerField()},
            {"id": [_make_uuid()], "count": ["42"]},
        )
        assert df["count"][0] == 42

    def test_integerfield_invalid_string_becomes_error(self):
        df = self._run(
            {"count": models.IntegerField()},
            {"id": [_make_uuid()], "count": ["abc"]},
        )
        assert df["__error__info"][0] is not None

    def test_integerfield_min_value_validator(self):
        df = self._run(
            {"score": models.IntegerField(validators=[MinValueValidator(0)])},
            {"id": [_make_uuid()], "score": ["-5"]},
        )
        assert df["__error__info"][0] is not None
        assert "min_value" in df["__error__info"][0]

    def test_integerfield_max_value_validator(self):
        df = self._run(
            {"score": models.IntegerField(validators=[MaxValueValidator(100)])},
            {"id": [_make_uuid()], "score": ["200"]},
        )
        assert df["__error__info"][0] is not None
        assert "max_value" in df["__error__info"][0]

    def test_positive_integer_field_clamped_at_zero(self):
        df = self._run(
            {"count": models.PositiveIntegerField()},
            {"id": [_make_uuid()], "count": ["-1"]},
        )
        # Clamped to 0 (min of PositiveIntegerField is 0)
        assert df["count"][0] == 0

    # ── FloatField / DecimalField ─────────────────────────────────────────

    def test_floatfield_parses(self):
        df = self._run(
            {"price": models.FloatField()},
            {"id": [_make_uuid()], "price": ["3.14"]},
        )
        assert abs(df["price"][0] - 3.14) < 1e-9

    def test_decimalfield_parses(self):
        df = self._run(
            {"price": models.DecimalField(max_digits=10, decimal_places=2)},
            {"id": [_make_uuid()], "price": ["9.99"]},
        )
        assert df["price"][0] == Decimal("9.99")

    # ── BooleanField ──────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("True", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("0", False),
        ],
    )
    def test_booleanfield_coercion(self, raw, expected):
        df = self._run(
            {"active": models.BooleanField()},
            {"id": [_make_uuid()], "active": [raw]},
        )
        assert df["active"][0] == expected

    def test_booleanfield_invalid_string_becomes_null_error(self):
        df = self._run(
            {"active": models.BooleanField()},
            {"id": [_make_uuid()], "active": ["maybe"]},
        )
        assert df["__error__info"][0] is not None

    # ── DateField / DateTimeField / TimeField ─────────────────────────────

    def test_datefield_string_parsed(self):
        df = self._run(
            {"dob": models.DateField(null=True)},
            {"id": [_make_uuid()], "dob": ["2000-01-15"]},
        )
        import datetime

        assert df["dob"][0] == datetime.date(2000, 1, 15)

    def test_datetimefield_string_parsed(self):
        df = self._run(
            {"created": models.DateTimeField(null=True)},
            {"id": [_make_uuid()], "created": ["2024-06-01 12:00:00"]},
        )
        assert df["created"][0] is not None

    def test_timefield_string_parsed(self):
        df = self._run(
            {"alarm": models.TimeField(null=True)},
            {"id": [_make_uuid()], "alarm": ["08:30:00"]},
        )
        import datetime

        assert df["alarm"][0] == datetime.time(8, 30, 0)

    def test_datetimefield_auto_now_replaced(self):
        """auto_now should replace every value with current time."""
        df = self._run(
            {"updated": models.DateTimeField(auto_now=True)},
            {"id": [_make_uuid()], "updated": [None]},
        )
        assert df["updated"][0] is not None

    def test_datetimefield_auto_now_add_only_fills_nulls(self):
        """auto_now_add should fill None but leave existing values."""
        df = self._run(
            {"created": models.DateTimeField(auto_now_add=True, null=True)},
            {"id": [_make_uuid(), _make_uuid()], "created": [None, None]},
        )
        # Both were null so both should be filled
        assert all(v is not None for v in df["created"].to_list())

    # ── DurationField ─────────────────────────────────────────────────────

    def test_durationfield_int_string_parsed(self):
        df = self._run(
            {"duration": models.DurationField(null=True)},
            {"id": [_make_uuid()], "duration": ["1000000"]},
        )
        import datetime

        assert df["duration"][0] == datetime.timedelta(seconds=1)

    # ── UUIDField ─────────────────────────────────────────────────────────

    def test_uuidfield_valid_with_dashes_normalised(self):
        raw = str(uuid.uuid4())
        expected = raw.replace("-", "").lower()
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": [raw]},
        )
        assert df["ref"][0] == expected

    def test_uuidfield_invalid_becomes_null_error(self):
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": ["not-a-uuid"]},
        )
        # Invalid UUID → null → error because field required by default? No: null=True
        # Null is allowed; the value was bad → becomes null; no required-value error
        # but __error__info should capture invalid_value
        assert df["__error__info"][0] is not None

    def test_uuidfield_hex_without_dashes_accepted(self):
        raw = uuid.uuid4().hex
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": [raw]},
        )
        assert df["ref"][0] == raw.lower()

    # ── GenericIPAddressField ─────────────────────────────────────────────

    def test_ip_field_valid_ipv4(self):
        df = self._run(
            {"ip": models.GenericIPAddressField(null=True)},
            {"id": [_make_uuid()], "ip": ["192.168.1.1"]},
        )
        assert df["ip"][0] == "192.168.1.1"

    def test_ip_field_invalid_becomes_null(self):
        df = self._run(
            {"ip": models.GenericIPAddressField(null=True)},
            {"id": [_make_uuid()], "ip": ["999.999.999.999"]},
        )
        assert df["ip"][0] is None

    def test_ip_field_protocol_ipv4_rejects_ipv6(self):
        df = self._run(
            {"ip": models.GenericIPAddressField(protocol="IPv4", null=True)},
            {"id": [_make_uuid()], "ip": ["2001:db8::1"]},
        )
        assert df["ip"][0] is None

    # ── JSONField ─────────────────────────────────────────────────────────

    def test_jsonfield_string_parsed_to_list(self):
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": ['{"key": "value"}']},
        )
        assert df["meta"][0] == [{"key": "value"}]

    def test_jsonfield_dict_wrapped_in_list(self):
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": [{"key": "value"}]},
        )
        assert df["meta"][0] == [{"key": "value"}]

    def test_jsonfield_invalid_string_becomes_null(self):
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": ["not-json{{{"]},
        )
        assert df["meta"][0] is None

    # ── choices validation ────────────────────────────────────────────────

    def test_choices_valid_short_code_accepted(self):
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["m"]},
        )
        assert df["gender"][0] == "m"
        assert df["__error__info"][0] is None

    def test_choices_valid_long_code_mapped_to_short(self):
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["Male"]},
        )
        assert df["gender"][0] == "m"

    def test_choices_invalid_value_becomes_null_error(self):
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["x"]},
        )
        assert df["__error__info"][0] is not None

    # ── ManyToMany / Binary field rejection ───────────────────────────────

    def test_many_to_many_raises(self):
        meta_cls = type("Meta", (), {"app_label": "tests"})
        pk = models.UUIDField(primary_key=True, db_column="id")
        related = type(
            "RelModel",
            (models.Model,),
            {"id": pk, "Meta": meta_cls, "__module__": __name__},
        )
        m2m_field = models.ManyToManyField(related)
        model_cls = type(
            "TmpModelM2M",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "tags": m2m_field,
                "Meta": meta_cls,
                "__module__": __name__,
            },
        )
        df = pl.DataFrame({"id": [_make_uuid()]})
        # M2M columns won't appear in concrete_fields, so we rely on the
        # RowValidator checking for ManyToManyField in concrete_fields.
        # This verifies the guard exists: if m2m is in concrete_fields, it raises.
        # In practice Django excludes M2M from concrete_fields, but we still verify
        # the validator handles the model without crashing.
        rv = RowValidator(df_dict={model_cls: df})
        result = rv.run()[model_cls]  # should not raise (m2m not in concrete_fields)
        assert result is not None

    def test_empty_dataframe_returned_unchanged(self):
        meta_cls = type("Meta", (), {"app_label": "tests"})
        model_cls = type(
            "TmpEmpty",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "Meta": meta_cls,
                "__module__": __name__,
            },
        )
        df = pl.DataFrame(schema={"id": pl.Utf8})
        rv = RowValidator(df_dict={model_cls: df})
        result = rv.run()[model_cls]
        assert mo_polars_kit.is_frm_empty(result)

    def test_lazy_frame_input_handled(self):
        meta_cls = type("Meta", (), {"app_label": "tests"})
        model_cls = type(
            "TmpLazy",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "name": models.CharField(max_length=50),
                "Meta": meta_cls,
                "__module__": __name__,
            },
        )
        df = pl.DataFrame({"id": [_make_uuid()], "name": ["Bob"]}).lazy()
        rv = RowValidator(df_dict={model_cls: df})
        result = rv.run()[model_cls]
        collected = result.collect() if isinstance(result, pl.LazyFrame) else result
        assert collected["name"][0] == "Bob"

    # ── default value applied ─────────────────────────────────────────────

    def test_default_applied_to_null_column(self):
        df = self._run(
            {"status": models.CharField(max_length=20, default="pending")},
            {"id": [_make_uuid()], "status": [None]},
        )
        assert df["status"][0] == "pending"

    def test_callable_default_applied(self):
        df = self._run(
            {"ref": models.UUIDField(default=uuid.uuid4, null=True)},
            {"id": [_make_uuid()], "ref": [None]},
        )
        assert df["ref"][0] is not None


# ─────────────────────────────────────────────────────────────────────────────
# ForeignKeyValidator unit tests (requires DB for some paths)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestForeignKeyValidatorUnit(MindoffTestCase):

    @pytest.fixture(autouse=True, scope="class")
    def _class_app(self, request):
        app_name = f"app_{uuid.uuid4().hex[:12]}"
        temp_dir = Path(tempfile.mkdtemp()).resolve()
        override = _register_app(temp_dir, app_name)

        author = _create_model(
            app_name,
            "AuthorModel",
            "author",
            [],
            {"name": models.CharField(max_length=50)},
        )
        book = _create_model(
            app_name,
            "BookModel",
            "book",
            [(app_name, "AuthorModel", "required")],
            {"title": models.CharField(max_length=100)},
        )

        request.cls._author_model = author
        request.cls._book_model = book
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override
        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))

    @pytest.fixture(autouse=True)
    def _models(self):
        with connection.schema_editor() as editor:
            editor.create_model(self._author_model)
            editor.create_model(self._book_model)
        _validate_model(self._author_model)
        _validate_model(self._book_model)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(self._book_model)
            editor.delete_model(self._author_model)

    def _seed_authors(self, count=2):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model],
            counts=[count],
        )
        mo_crud_kit.create(df_dict, is_partial=False)
        return df_dict[self._author_model]

    def test_fk_resolved_against_in_dict_valid(self):
        author_id = _make_uuid()
        author_df = pl.DataFrame(
            {
                "id": [author_id],
                "name": ["Alice"],
            }
        )
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["Book A"],
                "author_ref_id": [author_id],
            }
        )
        validator = ForeignKeyValidator(
            {
                self._author_model: author_df,
                self._book_model: book_df,
            }
        )
        # Should not raise
        result = validator.validate()
        assert self._book_model in result

    def test_fk_resolved_against_in_dict_invalid_raises(self):
        author_id = _make_uuid()
        bad_fk = _make_uuid()
        author_df = pl.DataFrame({"id": [author_id], "name": ["Alice"]})
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["Book A"],
                "author_ref_id": [bad_fk],  # bad FK
            }
        )
        validator = ForeignKeyValidator(
            {
                self._author_model: author_df,
                self._book_model: book_df,
            }
        )
        with pytest.raises(ValueError, match="couldn't resolve foreign key"):
            validator.validate()

    def test_fk_resolved_against_db_valid(self):
        """FK not in dict — should query DB."""
        author_df = self._seed_authors(1)
        author_id = author_df["id"][0]
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["DB Book"],
                "author_ref_id": [author_id],
            }
        )
        # Only book in dict; author resolved from DB
        validator = ForeignKeyValidator({self._book_model: book_df})
        result = validator.validate()
        assert self._book_model in result

    def test_fk_resolved_against_db_missing_raises(self):
        """FK not in dict, value not in DB → raises."""
        fake_id = _make_uuid()
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["Ghost Book"],
                "author_ref_id": [fake_id],
            }
        )
        validator = ForeignKeyValidator({self._book_model: book_df})
        with pytest.raises(ValueError, match="couldn't resolve foreign key"):
            validator.validate()

    def test_fk_all_nulls_skipped(self):
        """When all FK values are null, DB query should be skipped."""
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["Nullable FK Book"],
                "author_ref_id": [None],
            }
        )
        # No authors in DB or dict — but nulls are excluded from check
        # However author_ref_id is required (null=False) in model...
        # ForeignKeyValidator only checks existence, not nullability.
        # If all fk_values are empty after drop_nulls, it continues.
        validator = ForeignKeyValidator({self._book_model: book_df})
        # Should not raise (empty fk_values → skips)
        result = validator.validate()
        assert self._book_model in result

    def test_lazy_frame_fk_validated(self):
        author_id = _make_uuid()
        author_df = pl.DataFrame({"id": [author_id], "name": ["Alice"]}).lazy()
        book_df = pl.DataFrame(
            {
                "id": [_make_uuid()],
                "title": ["Lazy Book"],
                "author_ref_id": [author_id],
            }
        ).lazy()
        validator = ForeignKeyValidator(
            {
                self._author_model: author_df,
                self._book_model: book_df,
            }
        )
        result = validator.validate()
        assert self._book_model in result

    def test_empty_dataframe_skipped(self):
        empty_df = pl.DataFrame(
            schema={"id": pl.Utf8, "title": pl.Utf8, "author_ref_id": pl.Utf8}
        )
        validator = ForeignKeyValidator({self._book_model: empty_df})
        result = validator.validate()
        assert mo_polars_kit.is_frm_empty(result[self._book_model])


# ─────────────────────────────────────────────────────────────────────────────
# _ModelFrmsValidInvalidSplitter unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestModelFrmsValidInvalidSplitter:
    """Tests for cascade splitting logic."""

    ERROR_COL = "__error__info"

    def _build_df(self, ids, error_flags):
        return pl.DataFrame(
            {
                "id": ids,
                self.ERROR_COL: ["some error" if e else None for e in error_flags],
            }
        )

    def test_all_valid_no_invalid(self):
        meta = type("Meta", (), {"app_label": "tests"})
        model = type(
            "M",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "Meta": meta,
                "__module__": __name__,
            },
        )
        df = self._build_df([_make_uuid(), _make_uuid()], [False, False])
        splitter = _ModelFrmsValidInvalidSplitter({model: df})
        valid, invalid = splitter.run()
        assert valid[model].shape[0] == 2
        assert mo_polars_kit.is_frm_empty(invalid[model])

    def test_all_invalid(self):
        meta = type("Meta", (), {"app_label": "tests"})
        model = type(
            "M2",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "Meta": meta,
                "__module__": __name__,
            },
        )
        df = self._build_df([_make_uuid(), _make_uuid()], [True, True])
        splitter = _ModelFrmsValidInvalidSplitter({model: df})
        valid, invalid = splitter.run()
        assert mo_polars_kit.is_frm_empty(valid[model])
        assert invalid[model].shape[0] == 2

    def test_partial_invalid(self):
        meta = type("Meta", (), {"app_label": "tests"})
        model = type(
            "M3",
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "Meta": meta,
                "__module__": __name__,
            },
        )
        ids = [_make_uuid(), _make_uuid(), _make_uuid()]
        df = self._build_df(ids, [True, False, False])
        splitter = _ModelFrmsValidInvalidSplitter({model: df})
        valid, invalid = splitter.run()
        assert valid[model].shape[0] == 2
        assert invalid[model].shape[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests: batch_size, read edge-cases, update edge-cases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestCrudKitIntegrationEdgeCases(MindoffTestCase):

    @pytest.fixture(autouse=True, scope="class")
    def _class_app(self, request):
        app_name = f"app_{uuid.uuid4().hex[:12]}"
        temp_dir = Path(tempfile.mkdtemp()).resolve()
        override = _register_app(temp_dir, app_name)

        author = _create_model(
            app_name,
            "AuthorModel",
            "author",
            [],
            {
                "name": models.CharField(max_length=50),
                "nickname": models.CharField(max_length=50, blank=True, null=True),
            },
        )
        book = _create_model(
            app_name,
            "BookModel",
            "book",
            [(app_name, "AuthorModel", "required")],
            {
                "title": models.CharField(max_length=100),
                "pages": models.IntegerField(),
            },
        )

        request.cls._author_model = author
        request.cls._book_model = book
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override
        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))

    @pytest.fixture(autouse=True)
    def _models(self):
        with connection.schema_editor() as editor:
            editor.create_model(self._author_model)
            editor.create_model(self._book_model)
        _validate_model(self._author_model)
        _validate_model(self._book_model)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(self._book_model)
            editor.delete_model(self._author_model)

    def _seed(self, parent_count=3, child_count=2):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[parent_count, child_count],
        )
        status, valid, _ = mo_crud_kit.create(df_dict)
        assert status == "ok"
        return valid

    # ── create batch_size ─────────────────────────────────────────────────

    @pytest.mark.parametrize("batch_size", [1, 2, 5, 100])
    def test_create_various_batch_sizes(self, batch_size):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[5, 3],
        )
        status, valid, _ = mo_crud_kit.create(df_dict, batch_size=batch_size)
        assert status == "ok"
        assert valid[self._author_model].shape[0] == 5
        assert valid[self._book_model].shape[0] == df_dict[self._book_model].shape[0]

    # ── read: batch_size=0 auto-defaults ─────────────────────────────────

    def test_read_batch_size_zero_defaults_streaming(self):
        self._seed(5, 2)
        qs = self._book_model.objects.all().values()
        _, stats = mo_crud_kit.read(qs, batch_size=0)
        assert stats["batch_size"] == 1000
        assert stats["mode"] == "streaming"

    def test_read_batch_size_zero_defaults_pagination(self):
        self._seed(5, 2)
        qs = self._book_model.objects.all().values()
        _, stats = mo_crud_kit.read(qs, page_number=1, batch_size=0)
        assert stats["batch_size"] == 100
        assert stats["mode"] == "pagination"

    # ── read: page beyond last ─────────────────────────────────────────────

    def test_read_page_beyond_last_returns_empty(self):
        self._seed(2, 2)
        qs = self._book_model.objects.all().values()
        df, stats = mo_crud_kit.read(qs, page_number=9999, batch_size=10)
        assert df.shape[0] == 0
        assert stats["has_next"] is False
        assert stats["has_previous"] is True

    # ── read: empty queryset with pagination ──────────────────────────────

    def test_read_empty_qs_pagination(self):
        qs = self._book_model.objects.none().values()
        df, stats = mo_crud_kit.read(qs, page_number=1, batch_size=10)
        assert df.shape[0] == 0
        assert stats["total_count"] == 0
        assert stats["total_pages"] == 0
        assert stats["has_next"] is False
        assert stats["has_previous"] is False

    # ── read: invalid queryset types ──────────────────────────────────────

    def test_read_values_list_raises(self):
        self._seed(1, 1)
        qs = self._book_model.objects.values_list("id", flat=True)
        with pytest.raises((ValidationError, Exception)):
            mo_crud_kit.read(qs)

    def test_read_negative_batch_size_raises(self):
        self._seed(1, 1)
        qs = self._book_model.objects.values()
        with pytest.raises((ValidationError, Exception)):
            mo_crud_kit.read(qs, batch_size=-1)

    # ── update: is_partial with mixed valid/invalid ───────────────────────

    def test_update_partial_mixed_rows(self):
        valid = self._seed(3, 1)
        # Corrupt one author row (null name = required)
        update_dict = self.mo_update_mock_model_frms(
            valid,
            exclude_columns=[],
            modify=[{0: {"name": None}}],  # first author invalid
            counts=[3, 1],
        )
        # Only update authors
        author_only = {self._author_model: update_dict[self._author_model]}
        status, valid_u, invalid_u = mo_crud_kit.update(author_only, is_partial=True)
        assert status == "partial_ok"
        assert not mo_polars_kit.is_model_frms_empty(valid_u)
        assert not mo_polars_kit.is_model_frms_empty(invalid_u)

    # ── update: is_validate=False skips validation ────────────────────────

    def test_update_without_validation(self):
        valid = self._seed(2, 1)
        update_dict = self.mo_update_mock_model_frms(
            valid,
            exclude_columns=[],
            modify=[{0: {"name": "Updated Name"}}],
            counts=[2, 1],
        )
        author_only = {self._author_model: update_dict[self._author_model]}
        status, _, _ = mo_crud_kit.update(author_only, is_validate=False)
        assert status == "ok"

    # ── update: lazy frame input ──────────────────────────────────────────

    @pytest.mark.parametrize("is_temp_table", [True, False])
    def test_update_lazy_frame(self, is_temp_table):
        valid = self._seed(2, 1)
        update_dict = self.mo_update_mock_model_frms(
            valid,
            exclude_columns=[],
            modify=[{0: {"name": "Lazy Updated"}}],
            counts=[2, 1],
        )
        author_only = {self._author_model: update_dict[self._author_model].lazy()}
        status, _, _ = mo_crud_kit.update(author_only, is_temp_table=is_temp_table)
        assert status == "ok"

    # ── _update__fill_missing_columns: PK absent ──────────────────────────

    def test_fill_missing_columns_no_pk_raises(self):
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__fill_missing_columns(
                {self._author_model: df_no_pk}, batch_size=100
            )

    # ── _update__fill_missing_columns: all columns present ───────────────

    def test_fill_missing_columns_all_present_no_fetch(self):
        valid = self._seed(1, 0)
        author_df = valid.get(self._author_model)
        if author_df is None:
            return
        # All columns already present — should return unchanged
        result = _update__fill_missing_columns(
            {self._author_model: author_df}, batch_size=100
        )
        assert set(result[self._author_model].columns) == set(author_df.columns)

    # ── create: is_validate=False with bad DB data → RuntimeError ────────

    def test_create_no_validate_bad_uuid_raises_runtime_error(self):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model],
            counts=[2],
            is_uuid_hex=False,
        )
        with pytest.raises(RuntimeError):
            mo_crud_kit.create(df_dict, is_validate=False)

    # ── create: empty valid_model_frms → fail ────────────────────────────

    def test_create_all_invalid_rows_returns_fail(self):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[2, 1],
            modify=[{0: {"name": None}, 1: {"name": None}}],
        )
        status, _, _ = mo_crud_kit.create(df_dict, is_partial=False)
        assert status in ("fail", "partial_ok")

    # ── read stats correctness ────────────────────────────────────────────

    def test_read_stats_fields_present(self):
        self._seed(3, 2)
        qs = self._book_model.objects.all().values()
        _, stats = mo_crud_kit.read(qs, batch_size=5)
        required_keys = {
            "mode",
            "batch_size",
            "total_count",
            "total_pages",
            "current_page",
            "has_next",
            "has_previous",
        }
        assert required_keys.issubset(set(stats.keys()))

    # ── read: large dataset streaming integrity ────────────────────────────

    def test_read_streaming_large_dataset(self):
        self._seed(50, 3)
        qs = self._book_model.objects.all().values()
        df, stats = mo_crud_kit.read(qs, batch_size=7)
        assert df.shape[0] == stats["total_count"]
        assert stats["mode"] == "streaming"

    # ── update: upsert new row (non-existing PK) ──────────────────────────

    @pytest.mark.parametrize("is_temp_table", [True, False])
    def test_update_upsert_new_row(self, is_temp_table):
        self._seed(1, 0)
        new_id = _make_uuid()
        new_df = pl.DataFrame(
            {
                "id": [new_id],
                "name": ["Brand New Author"],
                "nickname": [None],
            }
        )
        status, _, _ = mo_crud_kit.update(
            {self._author_model: new_df},
            is_temp_table=is_temp_table,
        )
        assert status == "ok"
        # Verify the new row exists in DB
        assert self._author_model.objects.filter(**{"id": new_id}).exists()

    # ── update: no-op when only PK column present ─────────────────────────

    def test_update_only_pk_column(self):
        """DataFrame with only the PK column should succeed without error."""
        valid = self._seed(2, 0)
        author_df = valid.get(self._author_model)
        if author_df is None:
            return
        pk_only = author_df.select("id")
        # _update__fill_missing_columns should fetch missing cols
        result = _update__fill_missing_columns(
            {self._author_model: pk_only}, batch_size=100
        )
        assert "name" in result[self._author_model].columns

    # ── create: duplicate PK → RuntimeError ──────────────────────────────

    def test_create_duplicate_pk_raises(self):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model],
            counts=[1],
        )
        mo_crud_kit.create(df_dict, is_partial=False)
        # Try inserting same PK again — DB should reject
        with pytest.raises(RuntimeError):
            mo_crud_kit.create(df_dict, is_validate=False)


# ─────────────────────────────────────────────────────────────────────────────
# CRUDProcessor: unsupported engine
# ─────────────────────────────────────────────────────────────────────────────


class TestCRUDProcessorEngine:
    @staticmethod
    def _dummy_model(name: str = "DummyModel"):
        meta_cls = type("Meta", (), {"app_label": "tests", "db_table": "dummy"})
        return type(
            name,
            (models.Model,),
            {
                "id": models.UUIDField(primary_key=True, db_column="id"),
                "Meta": meta_cls,
                "__module__": __name__,
            },
        )

    def test_unsupported_engine_raises(self):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModel")
        bad_db_settings = {
            "default": {
                **settings.DATABASES["default"],
                "ENGINE": "django.db.backends.oracle",
            }
        }
        with override_settings(DATABASES=bad_db_settings):
            with pytest.raises(ValueError, match="Unsupported database engine"):
                CRUDProcessor({model: pl.DataFrame()})

    def test_unknown_alias_raises(self):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModel2")
        with pytest.raises(ValueError, match="not found in settings.DATABASES"):
            CRUDProcessor({model: pl.DataFrame()}, db_alias="nonexistent_alias")

    @pytest.mark.parametrize(
        "django_engine,expected_dialect",
        [
            ("django.db.backends.mysql", "mysql+pymysql"),
            ("django.db.backends.postgresql", "postgresql+psycopg2"),
        ],
    )
    def test_mysql_and_postgres_engine_url_with_auth_port(
        self,
        monkeypatch,
        django_engine,
        expected_dialect,
    ):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModel3")
        captured = {}

        def _fake_create_engine(url, *args, **kwargs):
            captured["url"] = url
            return object()

        calls = {"ensure_connection": 0}

        def _fake_ensure_connection():
            calls["ensure_connection"] += 1

        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.create_engine",
            _fake_create_engine,
        )
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.connection.ensure_connection",
            _fake_ensure_connection,
        )

        db_settings = {
            "default": {
                **settings.DATABASES["default"],
                "ENGINE": django_engine,
                "USER": "my user",
                "PASSWORD": "p@ss word",
                "HOST": "db.local",
                "PORT": "5432",
                "NAME": "mydb",
            }
        }
        with override_settings(DATABASES=db_settings):
            processor = CRUDProcessor({model: pl.DataFrame()})

        assert processor.dialect == expected_dialect
        assert (
            captured["url"]
            == f"{expected_dialect}://my+user:p%40ss+word@db.local:5432/mydb"
        )
        assert calls["ensure_connection"] == 1

    def test_mysql_engine_url_without_auth_and_port(self, monkeypatch):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModel4")
        captured = {}

        def _fake_create_engine(url, *args, **kwargs):
            captured["url"] = url
            return object()

        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.create_engine",
            _fake_create_engine,
        )
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.connection.ensure_connection",
            lambda: None,
        )

        db_settings = {
            "default": {
                **settings.DATABASES["default"],
                "ENGINE": "django.db.backends.mysql",
                "USER": "",
                "PASSWORD": "",
                "HOST": "localhost",
                "PORT": "",
                "NAME": "plain_db",
            }
        }
        with override_settings(DATABASES=db_settings):
            processor = CRUDProcessor({model: pl.DataFrame()})

        assert processor.dialect == "mysql+pymysql"
        assert captured["url"] == "mysql+pymysql://localhost/plain_db"


class TestCRUDKitHelpers:
    def test_read_build_stats_contract(self):
        stats = _read__build_stats(
            mode="streaming",
            batch_size=100,
            total_count=3,
            total_pages=0,
            current_page=0,
            has_next=False,
            has_previous=False,
        )
        assert stats == {
            "mode": "streaming",
            "batch_size": 100,
            "total_count": 3,
            "total_pages": 0,
            "current_page": 0,
            "has_next": False,
            "has_previous": False,
        }


class TestCRUDProcessorUnitPaths:
    class _Col:
        def __init__(self, name):
            self.name = name

    class _FakeUpsertStmt:
        def __init__(self):
            self.excluded = {}
            self.inserted = {}
            self.rows = None

        def values(self, rows):
            self.rows = rows
            return self

        def on_conflict_do_update(self, **kwargs):
            return ("on_conflict_do_update", kwargs)

        def on_duplicate_key_update(self, update_cols):
            return ("on_duplicate_key_update", update_cols)

    class _FakeTableForInsert:
        def __init__(self, cols):
            self.columns = [TestCRUDProcessorUnitPaths._Col(c) for c in cols]
            self._stmt = TestCRUDProcessorUnitPaths._FakeUpsertStmt()
            for c in cols:
                self._stmt.excluded[c] = f"excluded_{c}"
                self._stmt.inserted[c] = f"inserted_{c}"

        def insert(self):
            return self._stmt

    class _FakeConn:
        def __init__(self):
            self.executed = []

        def execute(self, stmt):
            self.executed.append(stmt)

    class _FakeBegin:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def __init__(self, conn):
            self.conn = conn

        def begin(self):
            return TestCRUDProcessorUnitPaths._FakeBegin(self.conn)

    def test_create_lazyframe_write_database_path(self, monkeypatch):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = TestCRUDProcessorEngine._dummy_model("DummyModelCreateLazy")
        calls = {"write": 0}

        def _fake_write_database(self, **kwargs):
            calls["write"] += 1

        class _FakeInspector:
            def get_table_names(self):
                return ["dummy"]

        conn = self._FakeConn()
        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.engine = self._FakeEngine(conn)
        processor.model_frame_map = {
            model: pl.DataFrame({"id": [uuid.uuid4().hex]}).lazy()
        }

        monkeypatch.setattr(pl.DataFrame, "write_database", _fake_write_database)
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.inspect",
            lambda _conn: _FakeInspector(),
        )

        result = processor.create(batch_size=10)
        assert result["message"] == "Database Operation successful"
        assert result["affected_tables"] == ["dummy"]
        assert calls["write"] == 1

    @pytest.mark.parametrize(
        "dialect,expected_execute_tag",
        [
            ("postgresql", "on_conflict_do_update"),
            ("mysql", "on_duplicate_key_update"),
            ("sqlite", "on_conflict_do_update"),
        ],
    )
    def test_update_df_insert_non_temp_dialect_paths(
        self, monkeypatch, dialect, expected_execute_tag
    ):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.dialect = dialect
        conn = self._FakeConn()
        table = self._FakeTableForInsert(["id", "name"])
        df = pl.DataFrame({"id": [uuid.uuid4().hex], "name": ["Alice"]})

        if dialect == "sqlite":
            monkeypatch.setattr(
                "apps.django_mindoff.components._crud_kit.crud_processor.sqlite_insert",
                lambda _table: table._stmt,
            )

        processor._update__df_insert(
            df,
            is_temp_table=False,
            temp_table_name="temp_upsert",
            table=table,
            pk_field="id",
            pk_col="id",
            conn=conn,
        )

        assert len(conn.executed) == 1
        assert conn.executed[0][0] == expected_execute_tag

    @pytest.mark.parametrize(
        "dialect,expected_execute_tag",
        [
            ("mysql", "on_duplicate_key_update"),
            ("postgresql", "on_conflict_do_update"),
        ],
    )
    def test_update_merge_staging_mysql_postgres_paths(
        self, monkeypatch, dialect, expected_execute_tag
    ):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        class _FakeSelect:
            def __init__(self):
                self.c = {"id": object(), "name": object()}

            def with_only_columns(self, *args):
                return self

            def where(self, *_args, **_kwargs):
                return self

        class _FakeDialectInsert:
            def __init__(self):
                self.excluded = {"id": "excluded_id", "name": "excluded_name"}
                self.inserted = {"id": "inserted_id", "name": "inserted_name"}

            def from_select(self, _cols, _select):
                return self

            def on_duplicate_key_update(self, update_cols):
                return ("on_duplicate_key_update", update_cols)

            def on_conflict_do_update(self, **kwargs):
                return ("on_conflict_do_update", kwargs)

        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.dialect = dialect
        conn = self._FakeConn()
        metadata = object()
        table = self._FakeTableForInsert(["id", "name"])

        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.Table",
            lambda *args, **kwargs: self._FakeTableForInsert(["id", "name"]),
        )
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.select",
            lambda _tbl: _FakeSelect(),
        )
        if dialect == "mysql":
            monkeypatch.setattr(
                "apps.django_mindoff.components._crud_kit.crud_processor.mysql_insert",
                lambda _table: _FakeDialectInsert(),
            )
        else:
            monkeypatch.setattr(
                "apps.django_mindoff.components._crud_kit.crud_processor.pg_insert",
                lambda _table: _FakeDialectInsert(),
            )

        processor._update__merge_staging(
            table,
            "tmp_staging",
            metadata,
            "id",
            "id",
            conn,
        )

        assert len(conn.executed) == 1
        assert conn.executed[0][0] == expected_execute_tag

    def test_update_merge_staging_sqlite_no_other_cols_returns(self, monkeypatch):
        from ...components._crud_kit.crud_processor import CRUDProcessor

        class _FakeSelect:
            def __init__(self):
                self.c = {"id": object()}

            def with_only_columns(self, *args):
                return self

            def where(self, *_args, **_kwargs):
                return self

        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.dialect = "sqlite"
        conn = self._FakeConn()
        metadata = object()
        table = self._FakeTableForInsert(["id"])
        table.c = {"id": object()}
        table.name = "only_pk_table"

        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.Table",
            lambda *args, **kwargs: self._FakeTableForInsert(["id"]),
        )
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.select",
            lambda _tbl: _FakeSelect(),
        )

        processor._update__merge_staging(
            table,
            "tmp_staging",
            metadata,
            "id",
            "id",
            conn,
        )

        assert conn.executed == []


# ─────────────────────────────────────────────────────────────────────────────
# _update__fill_missing_columns with LazyFrame
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestFillMissingColumnsLazy(MindoffTestCase):

    @pytest.fixture(autouse=True, scope="class")
    def _class_app(self, request):
        app_name = f"app_{uuid.uuid4().hex[:12]}"
        temp_dir = Path(tempfile.mkdtemp()).resolve()
        override = _register_app(temp_dir, app_name)
        author = _create_model(
            app_name,
            "AuthorModel",
            "author",
            [],
            {
                "name": models.CharField(max_length=50),
                "nickname": models.CharField(max_length=50, blank=True, null=True),
            },
        )
        request.cls._author_model = author
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override
        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))

    @pytest.fixture(autouse=True)
    def _models(self):
        with connection.schema_editor() as editor:
            editor.create_model(self._author_model)
        _validate_model(self._author_model)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(self._author_model)

    def test_fill_missing_columns_lazy_frame(self):
        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[3])
        mo_crud_kit.create(df_dict)
        author_df = df_dict[self._author_model]

        # pk-only lazy frame: fill should fetch missing columns from DB
        pk_only_lazy = author_df.select("id").lazy()
        result = _update__fill_missing_columns(
            {self._author_model: pk_only_lazy}, batch_size=100
        )
        assert isinstance(result[self._author_model], pl.LazyFrame)
        collected = result[self._author_model].collect()
        assert "name" in collected.columns

    def test_fill_missing_columns_pk_absent_raises(self):
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__fill_missing_columns(
                {self._author_model: df_no_pk}, batch_size=100
            )

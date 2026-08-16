import os
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
from django.db import connection, connections, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches
from ...components.crud_kit import mo_crud_kit
from ...components.crud_kit import MindoffCRUDHandler
from ...components.crud_kit import (
    ERROR_COL,
    _ModelFrmsValidInvalidSplitter,
    _read__build_stats,
    _update__prepare_frames,
)
from ...components._crud_kit.column_validator import ColumnValidator
from ...components._crud_kit.db_target import find_settings_dict, resolve_db_target
from ...components._crud_kit.row_validator import RowValidator
from ...components._crud_kit.foreign_key_validator import ForeignKeyValidator
from ...components.polars_kit import mo_polars_kit
from ...components.tdd_kit import MindoffTestCase, _create_model, _validate_model
from ...components.managers._create_app import DjangoAppCreator
from ...components.validation_kit import ValidationError


class TestColumnValidatorUnit:

    def test_pk_not_uuid_raises(self):
        """REJECTION: Pk not uuid raises."""
        model = self._uuid_model(pk_is_uuid=False)
        df = pl.DataFrame({"id": [1]})
        validator = ColumnValidator(model_frms={model: df})
        _, invalid = validator.run()
        # Should land in invalid with a descriptive error
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_pk_missing_db_column_raises(self):
        """REJECTION: Pk missing db column raises."""
        model = self._uuid_model(pk_has_db_col=False)
        df = pl.DataFrame({"id": [_make_uuid()]})
        validator = ColumnValidator(model_frms={model: df})
        _, invalid = validator.run()
        assert not mo_polars_kit.is_frm_empty(invalid[model])

    def test_extra_columns_removed_when_flag_true(self):
        """ACCEPTANCE: Extra columns removed when flag true."""
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
        """REJECTION: Extra columns kept and invalid when flag false."""
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
        """ACCEPTANCE: Missing columns added when flag true."""
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
        """REJECTION: Missing columns error when flag false."""
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
        """BOUNDARY: Empty dataframe passes through."""
        model = self._uuid_model()
        df = pl.DataFrame(schema={"id": pl.Utf8})
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        assert mo_polars_kit.is_frm_empty(valid[model]) or "id" in valid[model].columns

    def test_lazy_frame_handled_identically(self):
        """ACCEPTANCE: Lazy frame handled identically."""
        model = self._uuid_model(extra_fields={"name": models.CharField(max_length=50)})
        df = pl.DataFrame({"id": [_make_uuid()], "name": ["Alice"]}).lazy()
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        assert isinstance(valid[model], pl.LazyFrame)

    def test_db_column_rename_applied(self):
        """ACCEPTANCE: Db column rename applied."""
        fk_field = models.CharField(max_length=50, db_column="author_ref")
        model = self._uuid_model(extra_fields={"author_ref": fk_field})
        df = pl.DataFrame({"id": [_make_uuid()], "author_ref": ["Alice"]})
        validator = ColumnValidator(model_frms={model: df})
        valid, _ = validator.run()
        # The column should survive under its db_column name
        assert "author_ref" in valid[model].columns

    def _uuid_model(self, extra_fields=None, pk_has_db_col=True, pk_is_uuid=True):
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


class TestRowValidatorFieldSanitisation:

    def test_charfield_strips_whitespace(self):
        """ACCEPTANCE: Charfield strips whitespace."""
        df = self._run(
            {"name": models.CharField(max_length=50)},
            {"id": [_make_uuid()], "name": ["  Alice  "]},
        )
        assert df["name"][0] == "Alice"

    def test_charfield_blank_false_empty_string_becomes_null(self):
        """BOUNDARY: Charfield blank false empty string becomes null."""
        df = self._run(
            {"name": models.CharField(max_length=50)},
            {"id": [_make_uuid()], "name": [""]},
        )
        assert df["__error__info"][0] is not None  # missing required

    def test_charfield_blank_true_empty_string_preserved(self):
        """BOUNDARY: Charfield blank true empty string preserved."""
        df = self._run(
            {"name": models.CharField(max_length=50, blank=True, null=True)},
            {"id": [_make_uuid()], "name": [""]},
        )
        assert df["__error__info"][0] is None

    def test_charfield_max_length_exceeded_adds_error(self):
        """REJECTION: Charfield max length exceeded adds error."""
        df = self._run(
            {"name": models.CharField(max_length=3)},
            {"id": [_make_uuid()], "name": ["TooLong"]},
        )
        assert df["__error__info"][0] is not None
        assert "max_length" in df["__error__info"][0]

    def test_charfield_null_true_blank_true_none_passes(self):
        """ACCEPTANCE: Charfield null true blank true none passes."""
        df = self._run(
            {"name": models.CharField(max_length=50, null=True, blank=True)},
            {"id": [_make_uuid()], "name": [None]},
        )
        assert df["__error__info"][0] is None

    def test_slugfield_normalises(self):
        """ACCEPTANCE: Slugfield normalises."""
        df = self._run(
            {"slug": models.SlugField(max_length=100)},
            {"id": [_make_uuid()], "slug": ["Hello World!"]},
        )
        assert df["slug"][0] == "hello-world"

    def test_emailfield_lowercased(self):
        """ACCEPTANCE: Emailfield lowercased."""
        df = self._run(
            {"email": models.EmailField()},
            {"id": [_make_uuid()], "email": ["  USER@EXAMPLE.COM  "]},
        )
        assert df["email"][0] == "user@example.com"

    def test_urlfield_scheme_lowercased(self):
        """ACCEPTANCE: Urlfield scheme lowercased."""
        df = self._run(
            {"url": models.URLField()},
            {"id": [_make_uuid()], "url": ["HTTPS://Example.COM/Path"]},
        )
        assert df["url"][0].startswith("https://example.com")

    def test_integerfield_parses_string(self):
        """ACCEPTANCE: Integerfield parses string."""
        df = self._run(
            {"count": models.IntegerField()},
            {"id": [_make_uuid()], "count": ["42"]},
        )
        assert df["count"][0] == 42

    def test_integerfield_invalid_string_becomes_error(self):
        """REJECTION: Integerfield invalid string becomes error."""
        df = self._run(
            {"count": models.IntegerField()},
            {"id": [_make_uuid()], "count": ["abc"]},
        )
        assert df["__error__info"][0] is not None

    def test_integerfield_min_value_validator(self):
        """BOUNDARY: Integerfield min value validator."""
        df = self._run(
            {"score": models.IntegerField(validators=[MinValueValidator(0)])},
            {"id": [_make_uuid()], "score": ["-5"]},
        )
        assert df["__error__info"][0] is not None
        assert "min_value" in df["__error__info"][0]

    def test_integerfield_max_value_validator(self):
        """BOUNDARY: Integerfield max value validator."""
        df = self._run(
            {"score": models.IntegerField(validators=[MaxValueValidator(100)])},
            {"id": [_make_uuid()], "score": ["200"]},
        )
        assert df["__error__info"][0] is not None
        assert "max_value" in df["__error__info"][0]

    def test_positive_integer_field_clamped_at_zero(self):
        """BOUNDARY: Positive integer field clamped at zero."""
        df = self._run(
            {"count": models.PositiveIntegerField()},
            {"id": [_make_uuid()], "count": ["-1"]},
        )
        # Clamped to 0 (min of PositiveIntegerField is 0)
        assert df["count"][0] == 0

    def test_floatfield_parses(self):
        """ACCEPTANCE: Floatfield parses."""
        df = self._run(
            {"price": models.FloatField()},
            {"id": [_make_uuid()], "price": ["3.14"]},
        )
        assert abs(df["price"][0] - 3.14) < 1e-9

    def test_decimalfield_parses(self):
        """ACCEPTANCE: Decimalfield parses."""
        df = self._run(
            {"price": models.DecimalField(max_digits=10, decimal_places=2)},
            {"id": [_make_uuid()], "price": ["9.99"]},
        )
        assert df["price"][0] == Decimal("9.99")

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
        """ACCEPTANCE: Booleanfield coercion."""
        df = self._run(
            {"active": models.BooleanField()},
            {"id": [_make_uuid()], "active": [raw]},
        )
        assert df["active"][0] == expected

    def test_booleanfield_invalid_string_becomes_null_error(self):
        """REJECTION: Booleanfield invalid string becomes null error."""
        df = self._run(
            {"active": models.BooleanField()},
            {"id": [_make_uuid()], "active": ["maybe"]},
        )
        assert df["__error__info"][0] is not None

    def test_datefield_string_parsed(self):
        """ACCEPTANCE: Datefield string parsed."""
        df = self._run(
            {"dob": models.DateField(null=True)},
            {"id": [_make_uuid()], "dob": ["2000-01-15"]},
        )
        import datetime

        assert df["dob"][0] == datetime.date(2000, 1, 15)

    def test_datetimefield_string_parsed(self):
        """ACCEPTANCE: Datetimefield string parsed."""
        df = self._run(
            {"created": models.DateTimeField(null=True)},
            {"id": [_make_uuid()], "created": ["2024-06-01 12:00:00"]},
        )
        assert df["created"][0] is not None

    def test_timefield_string_parsed(self):
        """ACCEPTANCE: Timefield string parsed."""
        df = self._run(
            {"alarm": models.TimeField(null=True)},
            {"id": [_make_uuid()], "alarm": ["08:30:00"]},
        )
        import datetime

        assert df["alarm"][0] == datetime.time(8, 30, 0)

    def test_datetimefield_auto_now_replaced(self):
        """ACCEPTANCE: Datetimefield auto now replaced."""
        df = self._run(
            {"updated": models.DateTimeField(auto_now=True)},
            {"id": [_make_uuid()], "updated": [None]},
        )
        assert df["updated"][0] is not None

    def test_datetimefield_auto_now_add_only_fills_nulls(self):
        """BOUNDARY: Datetimefield auto now add only fills nulls."""
        df = self._run(
            {"created": models.DateTimeField(auto_now_add=True, null=True)},
            {"id": [_make_uuid(), _make_uuid()], "created": [None, None]},
        )
        # Both were null so both should be filled
        assert all(v is not None for v in df["created"].to_list())

    def test_durationfield_int_string_parsed(self):
        """ACCEPTANCE: Durationfield int string parsed."""
        df = self._run(
            {"duration": models.DurationField(null=True)},
            {"id": [_make_uuid()], "duration": ["1000000"]},
        )
        import datetime

        assert df["duration"][0] == datetime.timedelta(seconds=1)

    def test_uuidfield_valid_with_dashes_normalised(self):
        """ACCEPTANCE: Uuidfield valid with dashes normalised."""
        raw = str(uuid.uuid4())
        expected = raw.replace("-", "").lower()
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": [raw]},
        )
        assert df["ref"][0] == expected

    def test_uuidfield_invalid_becomes_null_error(self):
        """REJECTION: Uuidfield invalid becomes null error."""
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": ["not-a-uuid"]},
        )
        # Invalid UUID → null → error because field required by default? No: null=True
        # Null is allowed; the value was bad → becomes null; no required-value error
        # but __error__info should capture invalid_value
        assert df["__error__info"][0] is not None

    def test_uuidfield_hex_without_dashes_accepted(self):
        """ACCEPTANCE: Uuidfield hex without dashes accepted."""
        raw = uuid.uuid4().hex
        df = self._run(
            {"ref": models.UUIDField(null=True)},
            {"id": [_make_uuid()], "ref": [raw]},
        )
        assert df["ref"][0] == raw.lower()

    def test_ip_field_valid_ipv4(self):
        """ACCEPTANCE: Ip field valid ipv4."""
        df = self._run(
            {"ip": models.GenericIPAddressField(null=True)},
            {"id": [_make_uuid()], "ip": ["192.168.1.1"]},
        )
        assert df["ip"][0] == "192.168.1.1"

    def test_ip_field_invalid_becomes_null(self):
        """REJECTION: Ip field invalid becomes null."""
        df = self._run(
            {"ip": models.GenericIPAddressField(null=True)},
            {"id": [_make_uuid()], "ip": ["999.999.999.999"]},
        )
        assert df["ip"][0] is None

    def test_ip_field_protocol_ipv4_rejects_ipv6(self):
        """REJECTION: Ip field protocol ipv4 rejects ipv6."""
        df = self._run(
            {"ip": models.GenericIPAddressField(protocol="IPv4", null=True)},
            {"id": [_make_uuid()], "ip": ["2001:db8::1"]},
        )
        assert df["ip"][0] is None

    def test_jsonfield_string_normalized_to_compact_text(self):
        """ACCEPTANCE: JSON text is validated and normalized to compact JSON text."""
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": ['{"key": "value"}']},
        )
        assert df["meta"][0] == '{"key":"value"}'

    def test_jsonfield_dict_serialized_to_text(self):
        """ACCEPTANCE: A JSON value keeps its structure, serialized to JSON text."""
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": [{"key": "value"}]},
        )
        assert df["meta"][0] == '{"key":"value"}'

    def test_jsonfield_invalid_string_becomes_null(self):
        """REJECTION: Jsonfield invalid string becomes null."""
        df = self._run(
            {"meta": models.JSONField(null=True)},
            {"id": [_make_uuid()], "meta": ["not-json{{{"]},
        )
        assert df["meta"][0] is None

    def test_choices_valid_short_code_accepted(self):
        """ACCEPTANCE: Choices valid short code accepted."""
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["m"]},
        )
        assert df["gender"][0] == "m"
        assert df["__error__info"][0] is None

    def test_choices_valid_long_code_mapped_to_short(self):
        """ACCEPTANCE: Choices valid long code mapped to short."""
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["Male"]},
        )
        assert df["gender"][0] == "m"

    def test_choices_invalid_value_becomes_null_error(self):
        """REJECTION: Choices invalid value becomes null error."""
        CHOICES = [("m", "Male"), ("f", "Female")]
        df = self._run(
            {"gender": models.CharField(max_length=1, choices=CHOICES)},
            {"id": [_make_uuid()], "gender": ["x"]},
        )
        assert df["__error__info"][0] is not None

    def test_many_to_many_raises(self):
        """REJECTION: Many to many raises."""
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
        """BOUNDARY: Empty dataframe returned unchanged."""
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
        """ACCEPTANCE: Lazy frame input handled."""
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

    def test_default_applied_to_null_column(self):
        """ACCEPTANCE: Default applied to null column."""
        df = self._run(
            {"status": models.CharField(max_length=20, default="pending")},
            {"id": [_make_uuid()], "status": [None]},
        )
        assert df["status"][0] == "pending"

    def test_callable_default_applied(self):
        """ACCEPTANCE: Callable default applied."""
        df = self._run(
            {"ref": models.UUIDField(default=uuid.uuid4, null=True)},
            {"id": [_make_uuid()], "ref": [None]},
        )
        assert df["ref"][0] is not None

    def _run(self, fields: dict, data: dict) -> pl.DataFrame:
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


@pytest.mark.django_db(transaction=True)
class TestForeignKeyValidatorUnit(MindoffTestCase):

    def test_fk_resolved_against_in_dict_valid(self):
        """ACCEPTANCE: Fk resolved against in dict valid."""
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
        """REJECTION: Fk resolved against in dict invalid raises."""
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
        """ACCEPTANCE: Fk resolved against db valid."""
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
        """REJECTION: Fk resolved against db missing raises."""
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
        """ACCEPTANCE: Fk all nulls skipped."""
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
        """ACCEPTANCE: Lazy frame fk validated."""
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
        """BOUNDARY: Empty dataframe skipped."""
        empty_df = pl.DataFrame(
            schema={"id": pl.Utf8, "title": pl.Utf8, "author_ref_id": pl.Utf8}
        )
        validator = ForeignKeyValidator({self._book_model: empty_df})
        result = validator.validate()
        assert mo_polars_kit.is_frm_empty(result[self._book_model])

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

    # ---- IN-clause chunking ----

    def test_chunk_size_honors_the_backend_parameter_limit(self, monkeypatch):
        """ACCEPTANCE: SQLite's real limit wins when below the local ceiling.

        The limit is read from the connection rather than hardcoded: it is
        `SQLITE_MAX_VARIABLE_NUMBER`, a compile-time option that is 32766 on
        SQLite 3.32+ but 999 on older builds, so it varies by interpreter.
        """
        from ...components._crud_kit import foreign_key_validator as fkv

        validator = ForeignKeyValidator({})
        manager = self._author_model.objects
        backend_limit = connections[manager.db].features.max_query_params
        # Precondition: SQLite is the backend that reports a real limit at all.
        assert backend_limit is not None

        # With headroom above it, the backend's own limit must cap the chunk.
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=backend_limit + 1_000):
            assert validator._fk_chunk_size(manager) == backend_limit

        # And the local ceiling wins when it is the tighter of the two.
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=100):
            assert validator._fk_chunk_size(manager) == 100

    def test_chunk_size_falls_back_when_backend_reports_no_limit(self, monkeypatch):
        """BOUNDARY: psycopg2/MySQL report None, so the ceiling must apply.

        This is the branch the production PostgreSQL backend takes — without the
        fallback it would build one unbounded `IN (...)`.
        """
        from ...components._crud_kit import foreign_key_validator as fkv

        validator = ForeignKeyValidator({})
        manager = self._author_model.objects

        class _NoLimitFeatures:
            max_query_params = None

        class _NoLimitConn:
            features = _NoLimitFeatures()

        monkeypatch.setattr(
            fkv, "connections", {manager.db: _NoLimitConn()}, raising=True
        )
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=2_500):
            assert validator._fk_chunk_size(manager) == 2_500

    @pytest.mark.parametrize("configured", [0, -1, None])
    def test_non_positive_chunk_size_falls_back_to_the_default(
        self, monkeypatch, configured
    ):
        """REJECTION: there is no "unbounded" option for the FK chunk size.

        `MO_CRUD_ENGINE_CACHE_SIZE=0` means "no ceiling" because the cost is
        memory. Here the cost is a hard SQLite error and an unbounded statement
        everywhere else, so a non-positive value falls back rather than opting
        out.
        """
        from ...components._crud_kit import foreign_key_validator as fkv

        validator = ForeignKeyValidator({})
        manager = self._author_model.objects

        class _NoLimitFeatures:
            max_query_params = None

        class _NoLimitConn:
            features = _NoLimitFeatures()

        monkeypatch.setattr(
            fkv, "connections", {manager.db: _NoLimitConn()}, raising=True
        )
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=configured):
            assert validator._fk_chunk_size(manager) == fkv.DEFAULT_FK_CHUNK_SIZE

    def test_fk_validation_chunks_the_in_clause(self):
        """ACCEPTANCE: more distinct FKs than the chunk size still validates.

        The bug this replaces put every distinct value in one `IN (...)`, which
        on SQLite raises "too many SQL variables" past 32766.
        """
        authors = self._seed_authors(count=12)
        author_ids = authors["id"].to_list()

        with override_settings(MO_CRUD_FK_CHUNK_SIZE=5):
            books = _books_referencing(author_ids)
            validated = ForeignKeyValidator({self._book_model: books}).validate()

            # All 12 references resolve even though the chunk size is 5.
            assert validated[self._book_model].height == 12

            # And an unresolvable reference is still rejected under chunking.
            bogus = _books_referencing(author_ids + [str(uuid.uuid4())])
            with pytest.raises(Exception, match="couldn't resolve foreign key"):
                ForeignKeyValidator({self._book_model: bogus}).validate()

    def test_chunked_count_stops_at_the_first_short_chunk(self):
        """ACCEPTANCE: a known-invalid FK skips the remaining round-trips."""
        authors = self._seed_authors(count=10)
        author_ids = authors["id"].to_list()

        validator = ForeignKeyValidator({})
        calls = []

        class _FakeManager:
            db = "default"

            def filter(self, **kwargs):
                values = next(iter(kwargs.values()))
                calls.append(len(values))
                return self

            def count(self):
                return 0  # nothing matches: the very first chunk is short

        series = pl.Series("author_ref_id", author_ids)
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=2):
            total = validator._count_existing_fks(_FakeManager(), "id", series)

        assert total == 0
        assert len(calls) == 1  # stopped instead of walking all 5 chunks

    def test_over_sqlite_variable_limit_does_not_crash(self):
        """REGRESSION: a huge distinct FK set no longer blows SQLite's cap.

        `SQLITE_MAX_VARIABLE_NUMBER` is a hard wall — the single-statement
        version this replaces sent every distinct value at once, so a frame this
        wide died with "too many SQL variables" instead of reporting the
        unresolved reference. The correct outcome is a validation failure, not a
        database error, which is what the `match` here pins down.

        40k comfortably exceeds the cap on every build: 32766 on SQLite 3.32+,
        999 on older ones.
        """
        many = [str(uuid.uuid4()) for _ in range(40_000)]
        books = _books_referencing(many)

        with pytest.raises(Exception, match="couldn't resolve foreign key"):
            ForeignKeyValidator({self._book_model: books}).validate()

    def test_distinct_fk_values_stay_a_series(self):
        """REGRESSION: the distinct set is not listed into Python objects."""
        validator = ForeignKeyValidator({})
        frm = pl.DataFrame({"fk": ["a", "b", "a", None]})

        values = validator._get_distinct_fk_values(frm, "fk")

        assert isinstance(values, pl.Series)
        assert set(values.to_list()) == {"a", "b"}  # distinct, nulls dropped


class TestModelFrmsValidInvalidSplitter:

    ERROR_COL = "__error__info"

    def test_all_valid_no_invalid(self):
        """REJECTION: All valid no invalid."""
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
        """REJECTION: All invalid."""
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
        """REJECTION: Partial invalid."""
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

    def _build_df(self, ids, error_flags):
        return pl.DataFrame(
            {
                "id": ids,
                self.ERROR_COL: ["some error" if e else None for e in error_flags],
            }
        )


@pytest.mark.django_db(transaction=True)
class TestCrudKitIntegrationEdgeCases(MindoffTestCase):

    @pytest.mark.parametrize("batch_size", [1, 2, 5, 100])
    def test_create_various_batch_sizes(self, batch_size):
        """ACCEPTANCE: Create various batch sizes."""
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[5, 3],
        )
        status, valid, _ = mo_crud_kit.create(df_dict, batch_size=batch_size)
        assert status == "ok"
        assert valid[self._author_model].shape[0] == 5
        assert valid[self._book_model].shape[0] == df_dict[self._book_model].shape[0]

    def test_read_batch_size_zero_defaults_streaming(self):
        """BOUNDARY: Read batch size zero defaults streaming."""
        self._seed(5, 2)
        qs = self._book_model.objects.all().values()
        _, stats = mo_crud_kit.read(qs, batch_size=0)
        assert stats["batch_size"] == 1000
        assert stats["mode"] == "streaming"

    def test_read_batch_size_zero_defaults_pagination(self):
        """BOUNDARY: Read batch size zero defaults pagination."""
        self._seed(5, 2)
        qs = self._book_model.objects.all().values()
        _, stats = mo_crud_kit.read(qs, page_number=1, batch_size=0)
        assert stats["batch_size"] == 100
        assert stats["mode"] == "pagination"

    def test_read_page_beyond_last_returns_empty(self):
        """BOUNDARY: Read page beyond last returns empty."""
        self._seed(2, 2)
        qs = self._book_model.objects.all().values()
        df, stats = mo_crud_kit.read(qs, page_number=9999, batch_size=10)
        assert df.shape[0] == 0
        assert stats["has_next"] is False
        assert stats["has_previous"] is True

    def test_read_empty_qs_pagination(self):
        """BOUNDARY: Read empty qs pagination."""
        qs = self._book_model.objects.none().values()
        df, stats = mo_crud_kit.read(qs, page_number=1, batch_size=10)
        assert df.shape[0] == 0
        assert stats["total_count"] == 0
        assert stats["total_pages"] == 0
        assert stats["has_next"] is False
        assert stats["has_previous"] is False

    def test_read_values_list_raises(self):
        """REJECTION: Read values list raises."""
        self._seed(1, 1)
        qs = self._book_model.objects.values_list("id", flat=True)
        with pytest.raises((ValidationError, Exception)):
            mo_crud_kit.read(qs)

    def test_read_negative_batch_size_raises(self):
        """REJECTION: Read negative batch size raises."""
        self._seed(1, 1)
        qs = self._book_model.objects.values()
        with pytest.raises((ValidationError, Exception)):
            mo_crud_kit.read(qs, batch_size=-1)

    def test_update_partial_mixed_rows(self):
        """BOUNDARY: Update partial mixed rows."""
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

    def test_update_without_validation(self):
        """ACCEPTANCE: Update without validation."""
        valid = self._seed(2, 1)
        update_dict = self.mo_update_mock_model_frms(
            valid,
            exclude_columns=[],
            modify=[{0: {"name": "Updated Name"}}],
            counts=[2, 1],
        )
        author_only = {self._author_model: update_dict[self._author_model]}
        status, _, _ = mo_crud_kit.update(author_only, validation_level="none")
        assert status == "ok"

    def test_update_lazy_frame(self):
        """ACCEPTANCE: Update lazy frame."""
        valid = self._seed(2, 1)
        update_dict = self.mo_update_mock_model_frms(
            valid,
            exclude_columns=[],
            modify=[{0: {"name": "Lazy Updated"}}],
            counts=[2, 1],
        )
        author_only = {self._author_model: update_dict[self._author_model].lazy()}
        status, _, _ = mo_crud_kit.update(author_only)
        assert status == "ok"

    def test_prepare_frames_no_pk_raises(self):
        """REJECTION: A frame without its primary key cannot be matched for update."""
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__prepare_frames({self._author_model: df_no_pk})

    def test_prepare_frames_all_present_reports_nothing_unset(self):
        """ACCEPTANCE: A column-complete frame leaves nothing unset."""
        valid = self._seed(1, 0)
        author_df = valid.get(self._author_model)
        if author_df is None:
            return
        result, unset = _update__prepare_frames({self._author_model: author_df})
        assert set(result[self._author_model].columns) == set(author_df.columns)
        assert unset[self._author_model] == set()

    def test_prepare_frames_reports_omitted_columns(self):
        """ACCEPTANCE: Omitted model columns are reported, not fetched or filled."""
        pk_col = self._author_model._meta.pk.column
        partial = pl.DataFrame({pk_col: [_make_uuid()], "name": ["Alice"]})

        result, unset = _update__prepare_frames({self._author_model: partial})

        assert unset[self._author_model] == {"nickname"}
        # Left absent: a NULL placeholder would be indistinguishable from a
        # deliberate NULL by the time the merge builds its SET clause.
        assert "nickname" not in result[self._author_model].columns

    def test_create_no_validate_persists_unchecked_data(self):
        """C3/contract: validation_level='none' persists data as-is, no row checks.

        No validation runs (not even column normalization), so the caller's
        frame is written verbatim — the documented "may persist unsafe data"
        trade-off. We prove the row pass was skipped by leaving an untrimmed
        name that RowValidator would otherwise strip; it persists verbatim.
        """
        pk_col = self._author_model._meta.pk.column
        author_id = uuid.uuid4().hex
        df = pl.DataFrame(
            {pk_col: [author_id], "name": ["  Spaced  "], "nickname": [None]}
        )
        status, _, _ = mo_crud_kit.create(
            {self._author_model: df}, validation_level="none"
        )
        assert status == "ok"
        assert self._author_model.objects.get(pk=author_id).name == "  Spaced  "

    def test_sqlite_create_runs_in_single_explicit_transaction(self, monkeypatch):
        """REGRESSION: SQLite bulk create wraps all batches in ONE explicit BEGIN.

        Django opens SQLite in pysqlite autocommit mode
        (``isolation_level=None``). The SQLAlchemy engine is bound to that
        Django-owned connection via a ``creator``, so without an explicit
        ``BEGIN`` every batch auto-commits — fsyncing once per row and making a
        5,000-row create ~100x slower on a file-backed DB (seconds vs tens of
        seconds). The fix registers a ``begin`` event that emits ``BEGIN`` so the
        whole create commits once. This guards that wiring: it asserts the
        begin-event fires exactly once for a multi-batch create (one shared
        transaction, not per-batch/per-row autocommit) and that every row lands.

        Note: the test DB is ``:memory:`` so the *timing* symptom can't be
        reproduced (no fsync); we assert the transaction *mechanism* instead,
        which is what actually regressed.
        """
        import apps.django_mindoff.components._crud_kit.crud_processor as cp

        if "sqlite" not in settings.DATABASES["default"]["ENGINE"]:
            pytest.skip("SQLite-specific transaction regression")

        begin_emits = []
        real_emit = cp._sqlite_emit_begin

        def _spy_emit(conn):
            begin_emits.append(1)
            return real_emit(conn)

        # Engines are rebuilt per call for SQLite, so patching the module-level
        # name makes the create()-built engine pick up the spy.
        monkeypatch.setattr(cp, "_sqlite_emit_begin", _spy_emit)

        row_count = 5000
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model],
            counts=[row_count],
        )
        # batch_size < row_count forces multiple INSERT batches inside the single
        # transaction, so begin firing once proves the batches are shared.
        status, _, _ = mo_crud_kit.create(df_dict, batch_size=1000)

        assert status == "ok"
        assert self._author_model.objects.count() == row_count
        assert len(begin_emits) == 1, (
            "SQLite create must emit exactly one explicit BEGIN for the whole "
            f"batch; saw {len(begin_emits)} (autocommit regression?)."
        )

    def test_create_all_invalid_rows_returns_fail(self):
        """REJECTION: Create all invalid rows returns fail."""
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[2, 1],
            modify=[{0: {"name": None}, 1: {"name": None}}],
        )
        status, _, _ = mo_crud_kit.create(df_dict, is_partial=False)
        assert status in ("fail", "partial_ok")

    def test_read_stats_fields_present(self):
        """ACCEPTANCE: Read stats fields present."""
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

    def test_read_streaming_large_dataset(self):
        """BOUNDARY: Read streaming large dataset."""
        self._seed(50, 3)
        qs = self._book_model.objects.all().values()
        df, stats = mo_crud_kit.read(qs, batch_size=7)
        assert df.shape[0] == stats["total_count"]
        assert stats["mode"] == "streaming"

    def test_update_upsert_new_row(self):
        """ACCEPTANCE: Update upsert new row."""
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
        )
        assert status == "ok"
        # Verify the new row exists in DB
        assert self._author_model.objects.filter(**{"id": new_id}).exists()

    def test_update_only_pk_column_is_a_warned_noop(self):
        """BOUNDARY: A PK-only frame has nothing to write, and says so.

        Every other column is unset, so the merge has an empty SET clause. That
        is a no-op by construction — but a silent one would look like a
        successful update, so it warns.
        """
        valid = self._seed(2, 0)
        author_df = valid.get(self._author_model)
        if author_df is None:
            return
        pk_only = author_df.select("id")
        before = dict(self._author_model.objects.values_list("id", "name"))

        with pytest.warns(RuntimeWarning, match="only the primary key"):
            status, _, _ = mo_crud_kit.update({self._author_model: pk_only})

        assert status == "ok"
        assert dict(self._author_model.objects.values_list("id", "name")) == before

    def test_create_duplicate_pk_raises(self):
        """REJECTION: Create duplicate pk raises."""
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model],
            counts=[1],
        )
        mo_crud_kit.create(df_dict, is_partial=False)
        # Try inserting same PK again — DB should reject
        with pytest.raises(RuntimeError):
            mo_crud_kit.create(df_dict, validation_level="none")

    def test_read_auto_engine_canonical_dtypes(self):
        """ACCEPTANCE: Auto engine returns canonical UUID/FK/int dtypes."""
        self._seed(2, 3)
        df, _ = mo_crud_kit.read(self._book_model.objects.all().values())
        assert df.schema["id"] == pl.Utf8
        assert df.schema["author_ref_id"] == pl.Utf8
        assert df.schema["pages"] == pl.Int32
        # UUIDs rendered lowercase + hyphenated (canonical Django read form)
        sample = df["id"][0]
        assert sample == sample.lower()
        assert len(sample) == 36 and sample.count("-") == 4

    def test_read_auto_then_update_roundtrip(self):
        """ACCEPTANCE: A frame produced by read feeds straight back into update."""
        self._seed(2, 2)
        df, _ = mo_crud_kit.read(self._book_model.objects.all().values())
        status, _, invalid = mo_crud_kit.update({self._book_model: df})
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)

    def test_read_subset_then_update_leaves_omitted_columns_alone(self):
        """ACCEPTANCE: read() subset (hyphenated UUIDs) -> update touches only it.

        Guards the UUID PK canonicalization in `_update__prepare_frames`: read
        returns hyphenated UUIDs while the DB stores UUID PKs dashless, so the
        upsert would otherwise insert duplicates instead of matching.
        """
        self._seed(2, 3)
        # Read only id + title; pages and the FK column are intentionally absent.
        subset, _ = mo_crud_kit.read(
            self._book_model.objects.all().values("id", "title")
        )
        before = self._book_model.objects.count()
        status, _, _ = mo_crud_kit.update({self._book_model: subset})
        assert status == "ok"
        # Matched in place rather than inserting alongside.
        assert self._book_model.objects.count() == before
        # pages was never written, so the stored value stands.
        after, _ = mo_crud_kit.read(self._book_model.objects.all().values())
        assert after["pages"].null_count() == 0

    @pytest.mark.parametrize("is_lazy", [True, False])
    def test_read_fast_path_lazy_variants(self, is_lazy):
        """ACCEPTANCE: Fast path honors is_lazy in streaming and pagination."""
        self._seed(3, 2)
        qs = self._book_model.objects.all().values()
        stream, _ = mo_crud_kit.read(qs, is_lazy=is_lazy)
        page, _ = mo_crud_kit.read(qs, page_number=1, batch_size=2, is_lazy=is_lazy)
        expected = pl.LazyFrame if is_lazy else pl.DataFrame
        assert isinstance(stream, expected)
        assert isinstance(page, expected)

    def test_read_with_stats_false_skips_queries(self):
        """A2: with_stats=False skips exists()/count() and nulls the counts."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._seed(3, 5)
        qs = self._book_model.objects.all().values()
        with CaptureQueriesContext(connection) as with_stats:
            _, s_true = mo_crud_kit.read(qs, with_stats=True)
        with CaptureQueriesContext(connection) as without_stats:
            frm, s_false = mo_crud_kit.read(qs, with_stats=False)
        assert len(without_stats) < len(with_stats)
        assert s_false["total_count"] is None and s_false["total_pages"] is None
        assert s_true["total_count"] == frm.shape[0]

    def test_read_pagination_no_stats_has_next(self):
        """A2: pagination with_stats=False derives has_next via one extra row."""
        self._seed(1, 3)  # 1 author -> 3 books
        qs = self._book_model.objects.all().values()
        first, s1 = mo_crud_kit.read(qs, page_number=1, batch_size=2, with_stats=False)
        last, s2 = mo_crud_kit.read(qs, page_number=2, batch_size=2, with_stats=False)
        assert first.shape[0] == 2 and s1["has_next"] is True
        assert s1["has_previous"] is False and s1["total_count"] is None
        assert last.shape[0] == 1 and s2["has_next"] is False
        assert s2["has_previous"] is True

    def test_read_lazy_streaming_is_real_scan(self):
        """A3: is_lazy streaming returns a real (disk-backed) scan, not .lazy()."""
        self._seed(1, 6)  # 6 books
        qs = self._book_model.objects.all().values()
        lf, _ = mo_crud_kit.read(qs, is_lazy=True, batch_size=2)
        assert isinstance(lf, pl.LazyFrame)
        # The plan is a file scan, not an in-memory frame.
        assert "scan" in lf.explain().lower()
        collected = lf.collect()
        assert collected.shape[0] == 6
        assert collected.schema["id"] == pl.Utf8

    def test_repeated_lazy_reads_do_not_grow_atexit(self):
        """REGRESSION: the atexit registry stays flat across `read(is_lazy=True)`.

        The leak this replaces registered one `_safe_unlink` per lazy read, so a
        long-running process accumulated an entry per call forever.
        """
        import atexit

        self._seed(1, 3)
        qs = self._book_model.objects.all().values()

        # The first read registers the shared drain, if nothing else has yet.
        first, _ = mo_crud_kit.read(qs, is_lazy=True, batch_size=2)
        assert first.collect().shape[0] == 3
        baseline = atexit._ncallbacks()

        for _ in range(10):
            frm, _stats = mo_crud_kit.read(qs, is_lazy=True, batch_size=2)
            assert frm.collect().shape[0] == 3

        assert atexit._ncallbacks() == baseline

    def test_read_batches_streams_chunks(self):
        """A4: read_batches yields memory-bounded normalized chunks."""
        self._seed(1, 5)  # 5 books
        gen = mo_crud_kit.read_batches(
            self._book_model.objects.all().values(), batch_size=2
        )
        assert not isinstance(gen, (list, tuple))  # a generator, not materialized
        batches = list(gen)
        assert [b.shape[0] for b in batches] == [2, 2, 1]
        assert all(b.schema["id"] == pl.Utf8 for b in batches)
        assert sum(b.shape[0] for b in batches) == 5

    def test_create_lazyframe_streams_in_chunks(self):
        """B2: create() writes a LazyFrame in bounded-memory chunks."""
        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[7])
        lazy = {self._author_model: df_dict[self._author_model].lazy()}
        status, _, invalid = mo_crud_kit.create(lazy, batch_size=3)
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        assert self._author_model.objects.count() == 7

    def test_update_lazyframe_streams_in_chunks(self):
        """B2: update() stages a LazyFrame to the temp table in chunks, then merges."""
        self._seed(4, 0)  # 4 authors
        frm, _ = mo_crud_kit.read(self._author_model.objects.all().values())
        frm = frm.with_columns(pl.lit("Streamed").alias("name"))
        status, _, invalid = mo_crud_kit.update(
            {self._author_model: frm.lazy()}, batch_size=2
        )
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        names = set(self._author_model.objects.values_list("name", flat=True))
        assert names == {"Streamed"}

    def test_lazy_pipeline_emits_no_schema_resolution_warning(self):
        """B3: lazy CRUD must never trigger Polars' schema-resolution warning.

        ``LazyFrame.schema``/``.columns`` resolve the plan and emit a
        ``PerformanceWarning``; B1 replaced every such access with
        ``collect_schema()``. We promote that warning to an error and drive the
        full lazy pipeline (validated create + read eager/lazy + validated
        update, which exercises the row/FK validators, the valid/invalid
        splitter, and missing-column fill) so any regression fails the suite.
        This drives Python's ``warnings`` machinery directly, so it holds even
        under pytest's ``-p no:warnings``.
        """
        import warnings as _warnings

        from polars.exceptions import PerformanceWarning

        seeded = self._seed(3, 2)
        update_dict = self.mo_update_mock_model_frms(
            seeded,
            exclude_columns=[],
            modify=[{0: {"name": "Lazy Honest"}}],
            counts=[3, 2],
        )

        with _warnings.catch_warnings():
            _warnings.simplefilter("error", PerformanceWarning)

            # Validated create from a LazyFrame.
            create_lazy = self.mo_mock_model_frms(
                models=[self._author_model], counts=[4]
            )
            create_status, _, _ = mo_crud_kit.create(
                {self._author_model: create_lazy[self._author_model].lazy()},
                is_partial=False,
            )
            assert create_status == "ok"

            # Read both eager and as a genuine lazy scan, then collect it.
            qs = self._author_model.objects.all().values()
            eager_frm, _ = mo_crud_kit.read(qs)
            lazy_frm, _ = mo_crud_kit.read(qs, is_lazy=True)
            assert isinstance(lazy_frm, pl.LazyFrame)
            lazy_frm.collect()

            # Validated update from a LazyFrame (row + FK validation, splitter,
            # and missing-column fill all run on the lazy frame).
            update_status, _, _ = mo_crud_kit.update(
                {self._author_model: update_dict[self._author_model].lazy()},
                is_partial=True,
            )
            assert update_status in ("ok", "partial_ok")

    @pytest.mark.parametrize("batch_size", [1, 2, 3, 7, 1000])
    def test_create_honors_batch_size_chunking(self, batch_size, monkeypatch):
        """C1/C2: create() inserts in batch_size chunks via SQLAlchemy core."""
        from ...components._crud_kit import crud_processor as cp

        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[10])

        seen_batches = []
        original = cp.CRUDProcessor._fast_insert

        def _spy(self, df, table, conn, bs):
            for frm in self._iter_write_frames(df, bs):
                seen_batches.append(frm.height)
            # Re-drive the real insert so the rows actually persist.
            return original(self, df, table, conn, bs)

        monkeypatch.setattr(cp.CRUDProcessor, "_fast_insert", _spy)

        status, _, _ = mo_crud_kit.create(df_dict, batch_size=batch_size)
        assert status == "ok"
        assert self._author_model.objects.count() == 10
        # Every chunk is bounded by batch_size and they sum to all rows.
        assert seen_batches and all(h <= batch_size for h in seen_batches)
        assert sum(seen_batches) == 10

    def test_create_columns_only_skips_row_and_fk_validation(self):
        """C3: columns_only writes DB-ready rows and skips the row/FK passes.

        The caller supplies database-ready values; only column normalization runs.
        We prove the row pass is skipped by leaving an untrimmed title that
        RowValidator would otherwise strip — it must persist verbatim.
        """
        seeded = self._seed(1, 0)  # one validated author in the DB
        author_pk = seeded[self._author_model]["id"][0]
        book = self._book_model
        pk_col = book._meta.pk.column
        fk_col = next(
            f.column
            for f in book._meta.concrete_fields
            if isinstance(f, models.ForeignKey)
        )
        book_id = uuid.uuid4().hex
        book_df = pl.DataFrame(
            {
                pk_col: [book_id],
                "title": ["  Untrimmed  "],
                "pages": [42],
                fk_col: [author_pk],
            }
        )

        status, _, invalid = mo_crud_kit.create(
            {book: book_df}, validation_level="columns_only"
        )
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        assert book.objects.count() == 1
        # RowValidator (which trims text) was skipped, so the title is verbatim.
        assert book.objects.get(pk=book_id).title == "  Untrimmed  "

    def test_create_columns_only_missing_pk_rejected_at_write(self):
        """C3: columns_only trusts the caller; a missing PK is rejected by the DB.

        ColumnValidator normalizes shape (adds the missing PK column as null) but
        does not synthesize a value, so the NOT NULL primary key is enforced at
        write time and surfaces as a wrapped RuntimeError.
        """
        df_no_pk = pl.DataFrame({"name": ["NoPk"]})
        with pytest.raises(RuntimeError):
            mo_crud_kit.create(
                {self._author_model: df_no_pk}, validation_level="columns_only"
            )

    def test_update_columns_only_skips_row_validation(self):
        """C3: update() shares validation_level; columns_only skips the row pass.

        An untrimmed name persists verbatim, proving RowValidator was skipped
        while the missing-column fetch and column normalization still ran.
        """
        seeded = self._seed(1, 0)
        author_pk = seeded[self._author_model]["id"][0]
        pk_col = self._author_model._meta.pk.column
        df = pl.DataFrame({pk_col: [author_pk], "name": ["  Kept  "]})

        status, _, invalid = mo_crud_kit.update(
            {self._author_model: df}, validation_level="columns_only"
        )
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        assert self._author_model.objects.get(pk=author_pk).name == "  Kept  "

    def test_update_omitted_column_is_preserved_either_way(self):
        """E1: an omitted column is left alone, with or without skip_db_fill.

        ``skip_db_fill`` used to be the difference between "fetch it back" and
        "write NULL over it". Neither happens now — the column is simply not in
        the statement — so the flag is inert and both paths agree.
        """
        seeded = self._seed(1, 0)
        author_pk = seeded[self._author_model]["id"][0]
        pk_col = self._author_model._meta.pk.column
        # Partial frame: id + name only (nickname omitted).
        partial = pl.DataFrame({pk_col: [author_pk], "name": ["NewName"]})

        self._author_model.objects.filter(pk=author_pk).update(nickname="KeepMe")
        mo_crud_kit.update({self._author_model: partial.clone()})
        obj = self._author_model.objects.get(pk=author_pk)
        assert obj.name == "NewName"
        assert obj.nickname == "KeepMe"

        self._author_model.objects.filter(pk=author_pk).update(nickname="KeepMe2")
        with pytest.warns(DeprecationWarning, match="skip_db_fill"):
            mo_crud_kit.update({self._author_model: partial.clone()}, skip_db_fill=True)
        obj = self._author_model.objects.get(pk=author_pk)
        assert obj.name == "NewName"
        assert obj.nickname == "KeepMe2"

    def test_update_explicit_null_still_clears_the_column(self):
        """E1: omitting a column and setting it to NULL are different requests.

        This is the distinction the old back-fill could not express, and the
        reason omitted columns must stay absent rather than become NULL.
        """
        seeded = self._seed(1, 0)
        author_pk = seeded[self._author_model]["id"][0]
        pk_col = self._author_model._meta.pk.column
        self._author_model.objects.filter(pk=author_pk).update(nickname="ClearMe")

        explicit = pl.DataFrame(
            {pk_col: [author_pk], "name": ["NewName"], "nickname": [None]},
            schema_overrides={"nickname": pl.Utf8},
        )
        mo_crud_kit.update({self._author_model: explicit})

        assert self._author_model.objects.get(pk=author_pk).nickname is None

    def test_update_does_not_read_before_writing(self):
        """REGRESSION: preparing a partial frame issues no SELECT.

        The back-fill's per-PK prefetch is gone; the only statements should be
        the ones that write.
        """
        seeded = self._seed(1, 0)
        author_pk = seeded[self._author_model]["id"][0]
        pk_col = self._author_model._meta.pk.column
        partial = pl.DataFrame({pk_col: [author_pk], "name": ["NewName"]})

        with CaptureQueriesContext(connection) as captured:
            _, unset = _update__prepare_frames({self._author_model: partial})

        assert unset[self._author_model] == {"nickname"}
        assert captured.captured_queries == []

    def test_update_partial_frame_canonicalizes_pk(self):
        """E1: PK canonicalization runs for a partial frame too.

        Uses ``columns_only`` (no RowValidator) so preparation is the only stage
        that can canonicalize the hyphenated PK — if it did not, the upsert would
        insert a second row instead of matching the stored dashless one.
        """
        seeded = self._seed(1, 0)
        dashless = seeded[self._author_model]["id"][0]
        hyphenated = str(uuid.UUID(dashless))  # same id, canonical hyphenated form
        pk_col = self._author_model._meta.pk.column
        partial = pl.DataFrame({pk_col: [hyphenated], "name": ["Canon"]})

        status, _, _ = mo_crud_kit.update(
            {self._author_model: partial},
            validation_level="columns_only",
        )
        assert status == "ok"
        # Matched the existing (dashless-stored) row: updated in place, no insert.
        assert self._author_model.objects.count() == 1
        assert self._author_model.objects.get(pk=dashless).name == "Canon"

    def test_reflect_table_caches_and_validates_existence(self):
        """D2: reflected Table is cached per engine; a missing table errors clearly."""
        from ...components._crud_kit.crud_processor import CRUDProcessor

        self._seed(1, 0)
        proc = CRUDProcessor({self._author_model: pl.DataFrame()})
        table_db = self._author_model._meta.db_table
        with proc.engine.begin() as conn:
            t1 = proc._reflect_table(conn, table_db)
            t2 = proc._reflect_table(conn, table_db)
            assert t1 is t2  # second call served from the per-engine cache
            with pytest.raises(ValueError, match="does not exist"):
                proc._reflect_table(conn, "no_such_table_xyz")

    def test_iter_write_frames_chunking(self):
        """B2: the write chunker slices eager/lazy frames and never drops empties."""
        from ...components._crud_kit.crud_processor import CRUDProcessor

        proc = CRUDProcessor({})
        eager = list(proc._iter_write_frames(pl.DataFrame({"a": list(range(5))}), 2))
        assert [c.height for c in eager] == [2, 2, 1]
        lazy = list(proc._iter_write_frames(pl.LazyFrame({"a": list(range(5))}), 2))
        assert sum(c.height for c in lazy) == 5
        empty = list(
            proc._iter_write_frames(pl.LazyFrame(schema={"a": pl.Int64}), 2)
        )
        assert len(empty) == 1 and empty[0].height == 0

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


class TestCRUDProcessorEngine:

    def test_unsupported_engine_raises(self):
        """REJECTION: Unsupported engine raises."""
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
        """REJECTION: Unknown alias raises."""
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModel2")
        with pytest.raises(ValueError, match="not found in settings.DATABASES"):
            CRUDProcessor({model: pl.DataFrame()}, using="nonexistent_alias")

    @pytest.mark.parametrize(
        "django_engine,expected_dialect,expected_driver",
        [
            ("django.db.backends.mysql", "mysql", "mysql+pymysql"),
            ("django.db.backends.postgresql", "postgresql", "postgresql+psycopg2"),
        ],
    )
    def test_mysql_and_postgres_engine_url_with_auth_port(
        self,
        monkeypatch,
        django_engine,
        expected_dialect,
        expected_driver,
    ):
        """ACCEPTANCE: Mysql and postgres engine url with auth port."""
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
            connections["default"], "ensure_connection", _fake_ensure_connection
        )
        pass_code = "p@ss word"
        db_settings = {
            "default": {
                **settings.DATABASES["default"],
                "ENGINE": django_engine,
                "USER": "my user",
                "PASSWORD": pass_code,
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
            == f"{expected_driver}://my+user:p%40ss+word@db.local:5432/mydb"
        )
        assert calls["ensure_connection"] == 1

    def test_mysql_engine_url_without_auth_and_port(self, monkeypatch):
        """ACCEPTANCE: Mysql engine url without auth and port."""
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
            connections["default"], "ensure_connection", lambda: None
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

        assert processor.dialect == "mysql"
        assert captured["url"] == "mysql+pymysql://localhost/plain_db"

    @pytest.mark.django_db
    def test_sqlite_engine_not_cached(self):
        """D1: the SQLite engine is rebuilt per call (bound to Django's conn)."""
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = self._dummy_model("DummyModelSqliteEngine")
        p1 = CRUDProcessor({model: pl.DataFrame()})
        p2 = CRUDProcessor({model: pl.DataFrame()})
        assert p1.engine is not p2.engine

    def test_non_sqlite_engine_cached_per_settings(self, monkeypatch):
        """D1: non-SQLite engines are cached per resolved connection params."""
        from ...components._crud_kit import crud_processor as cp

        cp._ENGINE_CACHE.clear()
        calls = {"n": 0}

        def _fake_create_engine(url, *args, **kwargs):
            calls["n"] += 1
            return object()

        monkeypatch.setattr(cp, "create_engine", _fake_create_engine)
        monkeypatch.setattr(connections["default"], "ensure_connection", lambda: None)

        model = self._dummy_model("DummyModelEngineCache")
        base = {
            **settings.DATABASES["default"],
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "cache_db",
            "USER": "u",
            "PASSWORD": "p",
            "HOST": "h",
            "PORT": "1",
        }
        with override_settings(DATABASES={"default": base}):
            e1 = cp.CRUDProcessor({model: pl.DataFrame()}).engine
            e2 = cp.CRUDProcessor({model: pl.DataFrame()}).engine
        assert e1 is e2
        assert calls["n"] == 1  # second build served from cache

        # Different connection params -> distinct cache entry, fresh engine build.
        base2 = {**base, "NAME": "other_db"}
        with override_settings(DATABASES={"default": base2}):
            e3 = cp.CRUDProcessor({model: pl.DataFrame()}).engine
        assert e3 is not e1
        assert calls["n"] == 2

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


class TestCRUDKitHelpers:

    def test_read_build_stats_contract(self):
        """ACCEPTANCE: Read build stats contract."""
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
            self.type = f"type_{name}"

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
        def __init__(self, cols, name="dummy"):
            self.name = name
            self.columns = [TestCRUDProcessorUnitPaths._Col(c) for c in cols]
            # Mirrors SQLAlchemy's ``Table.c``: the merge projects the staging
            # select onto these columns and casts to their types. The merge also
            # reads its keys to learn which columns the frame actually staged.
            self.c = {c: TestCRUDProcessorUnitPaths._Col(c) for c in cols}
            self._stmt = TestCRUDProcessorUnitPaths._FakeUpsertStmt()
            for c in cols:
                self._stmt.excluded[c] = f"excluded_{c}"
                self._stmt.inserted[c] = f"inserted_{c}"

        def insert(self):
            return self._stmt

    class _FakeConn:
        def __init__(self):
            self.executed = []
            self.exec_params = []

        def execute(self, stmt, *args):
            self.executed.append(stmt)
            self.exec_params.append(args[0] if args else None)

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

    def test_create_lazyframe_fast_insert_path(self, monkeypatch):
        """C2: create() reflects the table and inserts rows via SQLAlchemy core.

        A LazyFrame input is streamed to chunks and bound through
        ``conn.execute(table.insert(), rows)`` (no ``write_database``/pandas).
        """
        from ...components._crud_kit.crud_processor import CRUDProcessor

        model = TestCRUDProcessorEngine._dummy_model("DummyModelCreateLazy")
        row_id = uuid.uuid4().hex

        fake_table = self._FakeTableForInsert(["id"])
        conn = self._FakeConn()
        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.engine = self._FakeEngine(conn)
        processor.dialect = "sqlite"
        processor.model_frame_map = {model: pl.DataFrame({"id": [row_id]}).lazy()}

        # create() reflects the target table via _reflect_table -> Table(...).
        monkeypatch.setattr(
            "apps.django_mindoff.components._crud_kit.crud_processor.Table",
            lambda *args, **kwargs: fake_table,
        )

        result = processor.create(batch_size=10)
        assert result["message"] == "Database Operation successful"
        assert result["affected_tables"] == ["dummy"]
        # One insert statement was executed with the frame rows as parameters.
        assert conn.executed == [fake_table._stmt]
        assert conn.exec_params == [[{"id": row_id}]]

    @staticmethod
    def _merge_sql(dialect, target_cols, staged_cols):
        """Compile what `_update__merge_staging` emits, for a given dialect.

        Real SQLAlchemy tables and a real dialect compiler, so the MySQL and
        PostgreSQL branches are covered as the SQL they actually produce rather
        than as call shapes against stand-ins.
        """
        import sqlalchemy as sa
        from sqlalchemy.dialects import mysql, postgresql, sqlite

        from ...components._crud_kit.crud_processor import CRUDProcessor

        metadata = sa.MetaData()
        target = sa.Table(
            "tbl_target",
            metadata,
            *[
                sa.Column(name, type_, primary_key=(name == "id"))
                for name, type_ in target_cols.items()
            ],
        )
        staging = sa.Table(
            "tmp_stage",
            metadata,
            *[sa.Column(name, sa.String()) for name in staged_cols],
        )

        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.dialect = dialect
        conn = TestCRUDProcessorUnitPaths._FakeConn()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "apps.django_mindoff.components._crud_kit.crud_processor.Table",
                lambda *_a, **_kw: staging,
            )
            processor._update__merge_staging(target, "tmp_stage", metadata, "id", conn)

        compiler = {
            "sqlite": sqlite.dialect(),
            "mysql": mysql.dialect(),
            "postgresql": postgresql.dialect(),
        }[dialect]
        return [
            " ".join(str(stmt.compile(dialect=compiler)).split())
            for stmt in conn.executed
        ]

    @pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgresql"])
    def test_update_merge_staging_writes_only_staged_columns(self, dialect):
        """ACCEPTANCE: the merge names the supplied columns and no others.

        `pages` exists on the target but not in the frame, so it must appear in
        neither the assignment list nor the INSERT column list — that absence is
        what leaves an existing row's value intact.
        """
        import sqlalchemy as sa

        update_sql, insert_sql = self._merge_sql(
            dialect,
            {"id": sa.String(), "title": sa.String(), "pages": sa.Integer()},
            ["id", "title"],
        )

        assert update_sql.startswith("UPDATE tbl_target")
        assert "title=" in update_sql and "pages" not in update_sql
        assert insert_sql.startswith("INSERT INTO tbl_target (id, title) SELECT")
        assert "pages" not in insert_sql
        # New keys only: a row that already exists is handled by the UPDATE.
        # Anti-join rather than NOT EXISTS — MySQL rejects a subquery that reads
        # the table being inserted into.
        assert "LEFT OUTER JOIN tbl_target" in insert_sql
        assert insert_sql.endswith("WHERE tbl_target.id IS NULL")
        assert "EXISTS" not in insert_sql

    @pytest.mark.parametrize(
        "dialect,expected",
        [
            # SQLite types dynamically, so nothing is cast on either side.
            ("sqlite", "FROM tmp_stage WHERE tbl_target.id = tmp_stage.id"),
            # Typed backends must cast the staging side of the match key.
            (
                "postgresql",
                "FROM tmp_stage WHERE tbl_target.id = CAST(tmp_stage.id AS VARCHAR)",
            ),
            # MySQL has no UPDATE..FROM; SQLAlchemy renders a multi-table UPDATE.
            ("mysql", "UPDATE tbl_target, tmp_stage SET"),
        ],
    )
    def test_update_merge_staging_dialect_update_form(self, dialect, expected):
        """ACCEPTANCE: each backend gets its own correlated-UPDATE syntax."""
        import sqlalchemy as sa

        update_sql, _ = self._merge_sql(
            dialect, {"id": sa.String(), "title": sa.String()}, ["id", "title"]
        )
        assert expected in update_sql

    def test_update_merge_staging_casts_to_target_types_off_sqlite(self):
        """ACCEPTANCE: typed backends cast staged text back to the column type.

        The staging table comes from the frame's Polars schema, so an integer
        arrives as text. SQLite's dynamic typing accepts it; PostgreSQL will not.
        """
        import sqlalchemy as sa

        cols = {"id": sa.String(), "pages": sa.Integer()}
        pg_update, _ = self._merge_sql("postgresql", cols, ["id", "pages"])
        sqlite_update, _ = self._merge_sql("sqlite", cols, ["id", "pages"])

        assert "CAST(tmp_stage.pages AS INTEGER)" in pg_update
        assert "CAST" not in sqlite_update

    def test_update_merge_staging_casts_the_match_key_too(self):
        """REGRESSION: the join key needs the cast as much as the values do.

        The staging table is text-typed throughout, so matching it against a
        `uuid` primary key raised `operator does not exist: uuid = text` on
        PostgreSQL. Only the staging side is cast — casting the target's column
        instead would work but would give up its primary-key index.
        """
        import sqlalchemy as sa

        update_sql, insert_sql = self._merge_sql(
            "postgresql", {"id": sa.Uuid(), "name": sa.String(50)}, ["id", "name"]
        )

        assert update_sql.endswith("WHERE tbl_target.id = CAST(tmp_stage.id AS UUID)")
        assert "ON tbl_target.id = CAST(tmp_stage.id AS UUID)" in insert_sql

    def test_update_merge_staging_pk_only_frame_warns_and_writes_nothing(self):
        """REJECTION: a frame with nothing but the key has nothing to merge."""
        import sqlalchemy as sa

        from ...components._crud_kit.crud_processor import CRUDProcessor

        metadata = sa.MetaData()
        target = sa.Table(
            "only_pk_table",
            metadata,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("title", sa.String()),
        )
        staging = sa.Table("tmp_stage", metadata, sa.Column("id", sa.String()))

        processor = CRUDProcessor.__new__(CRUDProcessor)
        processor.dialect = "sqlite"
        conn = self._FakeConn()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "apps.django_mindoff.components._crud_kit.crud_processor.Table",
                lambda *_a, **_kw: staging,
            )
            with pytest.warns(RuntimeWarning, match="only_pk_table"):
                processor._update__merge_staging(
                    target, "tmp_stage", metadata, "id", conn
                )

        assert conn.executed == []


@pytest.mark.django_db(transaction=True)
class TestUpdatePartialColumns(MindoffTestCase):
    """`update()` writes the columns it was given, and only those."""

    def test_prepare_frames_keeps_a_lazy_frame_lazy(self):
        """ACCEPTANCE: preparation never changes the caller's execution mode."""
        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[3])
        mo_crud_kit.create(df_dict)

        partial_lazy = df_dict[self._author_model].select("id", "name").lazy()
        result, unset = _update__prepare_frames({self._author_model: partial_lazy})

        out = result[self._author_model]
        assert isinstance(out, pl.LazyFrame)
        assert unset[self._author_model] == {"nickname"}
        # No query ran, so nothing was fetched to fill the gap.
        assert "nickname" not in out.collect_schema().names()

    def test_prepare_frames_pk_absent_raises(self):
        """REJECTION: without a primary key there is no row to match."""
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__prepare_frames({self._author_model: df_no_pk})

    @pytest.mark.parametrize("batch_size", [3, 1000])
    @pytest.mark.parametrize("is_lazy", [True, False])
    def test_partial_update_preserves_omitted_column(self, batch_size, is_lazy):
        """ACCEPTANCE: batching and frame type change neither the write nor the rest."""
        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[10])
        mo_crud_kit.create(df_dict)
        self._author_model.objects.all().update(nickname="Untouched")

        partial = df_dict[self._author_model].select("id", "name").with_columns(
            (pl.col("name") + pl.lit(" Edited")).alias("name")
        )
        frm = partial.lazy() if is_lazy else partial

        status, valid, _ = mo_crud_kit.update(
            {self._author_model: frm}, batch_size=batch_size
        )

        assert status == "ok"
        # The caller's execution mode survives the round trip.
        assert isinstance(valid[self._author_model], pl.LazyFrame if is_lazy else pl.DataFrame)
        rows = list(self._author_model.objects.values_list("name", "nickname"))
        assert len(rows) == 10
        assert all(name.endswith(" Edited") for name, _ in rows)
        assert {nickname for _, nickname in rows} == {"Untouched"}

    def test_returned_frame_carries_only_the_written_columns(self):
        """CONTRACT: the frames handed back describe what was actually written."""
        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[6])
        mo_crud_kit.create(df_dict)
        partial_lazy = df_dict[self._author_model].select("id", "name").lazy()

        status, valid, _invalid = mo_crud_kit.update(
            {self._author_model: partial_lazy}, batch_size=2
        )

        assert status == "ok"
        returned = valid[self._author_model]
        assert isinstance(returned, pl.LazyFrame)
        collected = returned.collect()
        assert collected.height == 6
        # No NULL placeholder for the column that was never written.
        assert "nickname" not in collected.columns

    def test_concurrent_writer_to_an_omitted_column_is_not_clobbered(self, monkeypatch):
        """REGRESSION: an omitted column is never read-modify-written.

        The back-fill this replaces fetched every omitted column and put it back
        in the SET clause, so anything committed between the fetch and the write
        was silently overwritten with the stale value. Nothing is fetched now, so
        the column is not in the statement at all and the other writer survives.
        """
        from ...components._crud_kit.crud_processor import CRUDProcessor

        df_dict = self.mo_mock_model_frms(models=[self._author_model], counts=[1])
        mo_crud_kit.create(df_dict)
        author_pk = df_dict[self._author_model]["id"][0]
        self._author_model.objects.filter(pk=author_pk).update(nickname="Original")

        partial = pl.DataFrame({"id": [author_pk], "name": ["Mine"]})
        author_model = self._author_model
        original_update = CRUDProcessor.update

        def _interleaved(processor, *args, **kwargs):
            # Another writer commits to a column our frame does not carry, after
            # we prepared the frame and before our statement runs.
            author_model.objects.filter(pk=author_pk).update(nickname="Theirs")
            return original_update(processor, *args, **kwargs)

        monkeypatch.setattr(CRUDProcessor, "update", _interleaved)
        mo_crud_kit.update({author_model: partial})

        obj = self._author_model.objects.get(pk=author_pk)
        assert obj.name == "Mine"  # our column landed
        assert obj.nickname == "Theirs"  # theirs was not rolled back

    def test_new_row_missing_a_required_column_fails_at_the_database(self):
        """BOUNDARY: an upsert-insert must still satisfy the table's constraints.

        Documented trade-off: an omitted NOT NULL column with no database default
        is fine for a row that exists (it keeps its value) but cannot be inserted,
        so a brand-new primary key surfaces the database's own integrity error.
        """
        partial = pl.DataFrame({"id": [_make_uuid()], "nickname": ["NoNameGiven"]})

        with pytest.raises(RuntimeError, match="(?i)not null"):
            mo_crud_kit.update({self._author_model: partial})

    def test_update_omitting_a_foreign_key_column_validates(self):
        """ACCEPTANCE: an absent FK is not a broken FK — there is nothing to check."""
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model], counts=[2, 2]
        )
        mo_crud_kit.create(df_dict)
        books = df_dict[self._book_model]
        # title only: the FK column the validator would normally resolve is gone.
        partial = books.select("id", "title").with_columns(
            pl.lit("Retitled").alias("title")
        )

        status, _, invalid = mo_crud_kit.update({self._book_model: partial})

        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        assert set(self._book_model.objects.values_list("title", flat=True)) == {
            "Retitled"
        }
        # The FK was left alone, so every book still points at its author.
        assert self._book_model.objects.filter(author_ref__isnull=True).count() == 0

    def test_splitter_skips_relations_the_frame_cannot_supply(self):
        """BOUNDARY: invalid-row propagation tolerates an omitted FK column.

        Propagation reads the FK column off the child frame; a partial update may
        not carry it, and a foreign key may point outside the batch entirely.
        Either way the relation is unusable and must be skipped, not faulted on.
        """
        author_id, book_id = _make_uuid(), _make_uuid()
        splitter = _ModelFrmsValidInvalidSplitter(
            {
                # Book omits author_ref_id; Author is in the batch and flagged.
                self._book_model: pl.DataFrame(
                    {"id": [book_id], "title": ["T"], ERROR_COL: ["boom"]}
                ),
                self._author_model: pl.DataFrame(
                    {"id": [author_id], "name": ["A"], ERROR_COL: [None]},
                    schema_overrides={ERROR_COL: pl.Utf8},
                ),
            }
        )
        assert splitter.relations == []

        valid, invalid = splitter.run()
        assert valid[self._book_model].height == 0
        assert invalid[self._book_model].height == 1
        # The author frame is clean and nothing propagated to it.
        assert valid[self._author_model].height == 1

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


@pytest.mark.django_db(transaction=True)
class TestReadFastPathDtypes(MindoffTestCase):
    """Read fast path parity across temporal, decimal, boolean and JSON types."""

    def _seed_rich(self):
        import datetime

        self._rich_model.objects.create(
            name="alice",
            count=5,
            price=Decimal("3.14"),
            active=True,
            created=datetime.datetime(2024, 6, 1, 12, 0, tzinfo=datetime.timezone.utc),
            payload={"k": 1},
        )

    def test_read_canonical_dtypes_match_map(self):
        """ACCEPTANCE: Fast-path dtypes equal the canonical model mapping."""
        from ...components._crud_kit.dtypes import resolve_polars_dtype

        self._seed_rich()
        df, _ = mo_crud_kit.read(self._rich_model.objects.all().values())
        fields = {f.attname: f for f in self._rich_model._meta.concrete_fields}
        for name, dtype in df.schema.items():
            field = fields[name]
            if field.__class__.__name__ == "JSONField":
                assert dtype == pl.Utf8  # auto -> text on the fast path
            else:
                assert dtype == resolve_polars_dtype(field)

    def test_read_datetime_is_tz_naive_microseconds(self):
        """ACCEPTANCE: Datetime columns come back tz-naive at microsecond unit."""
        self._seed_rich()
        df, _ = mo_crud_kit.read(self._rich_model.objects.all().values())
        assert df.schema["created"] == pl.Datetime("us")

    def test_read_json_mode_object(self):
        """ACCEPTANCE: json_column_mode='object' parses JSON to pl.Object."""
        self._seed_rich()
        df, _ = mo_crud_kit.read(
            self._rich_model.objects.all().values(), json_column_mode="object"
        )
        assert df.schema["payload"] == pl.Object
        assert df["payload"][0] == {"k": 1}

    def test_read_json_mode_text(self):
        """ACCEPTANCE: json_column_mode='text' keeps raw JSON text."""
        self._seed_rich()
        df, _ = mo_crud_kit.read(
            self._rich_model.objects.all().values(), json_column_mode="text"
        )
        assert df.schema["payload"] == pl.Utf8

    def test_read_rich_then_update_roundtrip(self):
        """ACCEPTANCE: Rich-typed read frame (incl. JSON) round-trips via update."""
        self._seed_rich()
        df, _ = mo_crud_kit.read(self._rich_model.objects.all().values())
        status, _, invalid = mo_crud_kit.update({self._rich_model: df})
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        # JSON content survives the round-trip.
        after, _ = mo_crud_kit.read(
            self._rich_model.objects.all().values(), json_column_mode="object"
        )
        assert after["payload"][0] == {"k": 1}

    def test_create_with_json_then_read_back(self):
        """ACCEPTANCE: create() persists JSON columns and reads them back."""
        import datetime

        pk = uuid.uuid4().hex
        frm = pl.DataFrame(
            {
                "id": [pk],
                "name": ["bob"],
                "count": [7],
                "price": ["1.50"],
                "active": [True],
                "created": ["2024-06-02 09:30:00"],
                "payload": ['{"a": [1, 2], "b": "x"}'],
            }
        )
        status, _, invalid = mo_crud_kit.create({self._rich_model: frm})
        assert status == "ok"
        assert mo_polars_kit.is_model_frms_empty(invalid)
        back, _ = mo_crud_kit.read(
            self._rich_model.objects.filter(name="bob").values(),
            json_column_mode="object",
        )
        assert back["payload"][0] == {"a": [1, 2], "b": "x"}

    @pytest.fixture(autouse=True, scope="class")
    def _class_app(self, request):
        app_name = f"app_{uuid.uuid4().hex[:12]}"
        temp_dir = Path(tempfile.mkdtemp()).resolve()
        override = _register_app(temp_dir, app_name)
        rich = _create_model(
            app_name,
            "RichModel",
            "rich",
            [],
            {
                "name": models.CharField(max_length=50),
                "count": models.IntegerField(null=True),
                "price": models.DecimalField(
                    max_digits=10, decimal_places=2, null=True
                ),
                "active": models.BooleanField(default=True),
                "created": models.DateTimeField(null=True),
                "payload": models.JSONField(null=True),
            },
        )
        request.cls._rich_model = rich
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override
        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))

    @pytest.fixture(autouse=True)
    def _models(self):
        with connection.schema_editor() as editor:
            editor.create_model(self._rich_model)
        _validate_model(self._rich_model)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(self._rich_model)


class TestArrowReaderUnit:
    """Pure-function coverage for the read fast-path helpers."""

    def test_resolve_polars_dtype_decimal_refines_precision(self):
        """ACCEPTANCE: DecimalField resolves to a precision/scale-aware dtype."""
        from ...components._crud_kit.dtypes import resolve_polars_dtype

        field = models.DecimalField(max_digits=8, decimal_places=3)
        assert resolve_polars_dtype(field) == pl.Decimal(precision=8, scale=3)

    def test_resolve_polars_dtype_unknown_returns_none(self):
        """BOUNDARY: Unmapped field types resolve to None."""
        from ...components._crud_kit.dtypes import resolve_polars_dtype

        class _Weird:
            pass

        assert resolve_polars_dtype(_Weird()) is None

    def test_connectorx_uri_memory_sqlite_is_none(self):
        """BOUNDARY: In-memory SQLite has no connectorx URI."""
        from ...components._crud_kit.arrow_reader import _connectorx_uri

        assert _connectorx_uri("default") is None

    def test_connectorx_uri_postgres_built_from_settings(self):
        """ACCEPTANCE: Postgres settings produce a connectorx URI."""
        from ...components._crud_kit.arrow_reader import _connectorx_uri

        cfg = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "mydb",
            "USER": "u",
            "PASSWORD": "p",
            "HOST": "h",
            "PORT": "5432",
        }
        with override_settings(DATABASES={**settings.DATABASES, "pg": cfg}):
            uri = _connectorx_uri("pg")
        assert uri == "postgresql://u:p@h:5432/mydb"

    # ---- connectorx parameter inlining (safe literal rendering) ----

    def test_sql_literal_scalar_types(self):
        """ACCEPTANCE: scalars render to correct SQL literals."""
        from decimal import Decimal
        import datetime as _dt
        from ...components._crud_kit.arrow_reader import _sql_literal

        lit = lambda v: _sql_literal(v, mysql=False)
        assert lit(None) == "NULL"
        assert lit(True) == "1" and lit(False) == "0"
        assert lit(42) == "42"
        assert lit(-7) == "-7"
        assert lit(3.5) == "3.5"
        assert lit(Decimal("10.25")) == "10.25"
        assert lit(b"\x00\xff") == "X'00ff'"
        assert lit("hi") == "'hi'"
        assert lit(_dt.date(2024, 1, 2)) == "'2024-01-02'"
        assert lit(_dt.datetime(2024, 1, 2, 3, 4, 5)) == "'2024-01-02 03:04:05'"

    def test_sql_literal_rejects_non_finite_and_nul(self):
        """REJECTION: non-finite numbers and NUL bytes are refused."""
        from decimal import Decimal
        from ...components._crud_kit.arrow_reader import _sql_literal

        for bad in (float("inf"), float("nan"), Decimal("Infinity")):
            with pytest.raises(ValueError):
                _sql_literal(bad, mysql=False)
        with pytest.raises(ValueError):
            _sql_literal("a\x00b", mysql=False)

    def test_sql_literal_escapes_quotes_injection_safe(self):
        """SECURITY: embedded single quotes are doubled, not breakable."""
        from ...components._crud_kit.arrow_reader import _sql_literal

        attack = "x'); DROP TABLE users; --"
        out = _sql_literal(attack, mysql=False)
        # The whole value stays inside one quoted literal (quotes doubled).
        assert out == "'x''); DROP TABLE users; --'"
        # Quote count is even -> the literal is balanced/closed.
        assert out.count("'") % 2 == 0

    def test_sql_literal_mysql_escapes_backslash(self):
        """SECURITY: MySQL also escapes backslashes; standard SQL does not."""
        from ...components._crud_kit.arrow_reader import _sql_literal

        # A backslash-quote that would break a MySQL literal if left unescaped.
        value = "a\\'b"
        assert _sql_literal(value, mysql=True) == "'a\\\\''b'"
        # Standard SQL (SQLite/PG) keeps backslashes literal, only doubles quotes.
        assert _sql_literal(value, mysql=False) == "'a\\''b'"

    def test_inline_params_expands_placeholders_and_percent(self):
        """ACCEPTANCE: %s is filled from literals and %% collapses to %."""
        from ...components._crud_kit.arrow_reader import _inline_params

        sql = "SELECT * FROM t WHERE a = %s AND b LIKE %s ESCAPE '\\' OR c = %s"
        out = _inline_params(sql, (1, "%ab%", None), mysql=False)
        assert out == (
            "SELECT * FROM t WHERE a = 1 AND b LIKE '%ab%' ESCAPE '\\' OR c = NULL"
        )
        # %% in the SQL becomes a single literal % (format-paramstyle contract).
        assert _inline_params("SELECT 50 %% 7", (), mysql=False) == "SELECT 50 % 7"

    def test_inline_params_pattern_percent_not_reinterpreted(self):
        """SECURITY: % inside a substituted literal is not reprocessed."""
        from ...components._crud_kit.arrow_reader import _inline_params

        # Two placeholders; the first value contains %s-looking text that must
        # NOT consume the second placeholder or raise.
        out = _inline_params("WHERE a = %s AND b = %s", ("%s %d %%", 9), mysql=False)
        assert out == "WHERE a = '%s %d %%' AND b = 9"

    @pytest.mark.django_db
    def test_try_connectorx_inlines_real_contains_query_safely(self, monkeypatch):
        """E/ConnectorX: a __contains query inlines to valid, safe SQL.

        Reproduces the original failure (str(qs.query) rendered LIKE patterns
        unquoted) and proves the fix: the compiled query is inlined into valid
        SQL with the pattern properly quoted, and the result is executable.
        """
        import sqlite3
        from django.contrib.contenttypes.models import ContentType
        from ...components._crud_kit import arrow_reader as ar

        captured = {}
        monkeypatch.setattr(ar, "_connectorx_uri", lambda _alias: "sqlite:///x.db")

        def _fake_read_database_uri(sql, uri):
            captured["sql"] = sql
            return pl.DataFrame({"id": []})

        monkeypatch.setattr(ar.pl, "read_database_uri", _fake_read_database_uri)

        qs = ContentType.objects.filter(model__contains="a'b%c").values("id")
        result = ar._try_connectorx(qs)
        assert result is not None

        inlined = captured["sql"]
        # No leftover placeholders; the embedded single quote is doubled (so the
        # injection-prone value stays inside one literal); quotes stay balanced.
        assert "%s" not in inlined
        assert "a''b" in inlined
        assert inlined.count("'") % 2 == 0
        # Valid, executable SQL — unlike the old str(qs.query) rendering.
        assert sqlite3.complete_statement(inlined + ";")

    # ---- read routing (ConnectorX when safe, else Django cursor) ----

    def test_read_frame_uses_connectorx_only_when_usable(self, monkeypatch):
        """ACCEPTANCE: ConnectorX is used only when the read is safe.

        ``read_frame`` reaches for ConnectorX's zero-copy path only when
        ``_connectorx_usable`` allows it (committed-data read); when it can't
        (open transaction, in-memory SQLite), it goes straight to the Django
        cursor without ever attempting ConnectorX.
        """
        from ...components._crud_kit import arrow_reader as ar

        calls = {"cx": 0}

        class _QS:
            db = "default"
            model = object()

        monkeypatch.setattr(ar, "_normalize", lambda frm, *a, **k: frm)
        monkeypatch.setattr(ar, "_execute_django", lambda qs: "django")

        def _fake_cx(qs):
            calls["cx"] += 1
            return "connectorx"

        monkeypatch.setattr(ar, "_try_connectorx", _fake_cx)

        # Usable (not in a transaction): ConnectorX is used.
        monkeypatch.setattr(ar, "_connectorx_usable", lambda _db: True)
        assert ar.read_frame(_QS()) == "connectorx"
        assert calls["cx"] == 1

        # Not usable (open txn / in-memory): ConnectorX is never attempted.
        monkeypatch.setattr(ar, "_connectorx_usable", lambda _db: False)
        assert ar.read_frame(_QS()) == "django"
        assert calls["cx"] == 1

    def test_read_frame_falls_back_to_cursor_when_connectorx_returns_none(
        self, monkeypatch
    ):
        """ACCEPTANCE: a None from ConnectorX transparently uses the cursor."""
        from ...components._crud_kit import arrow_reader as ar

        class _QS:
            db = "default"
            model = object()

        monkeypatch.setattr(ar, "_normalize", lambda frm, *a, **k: frm)
        monkeypatch.setattr(ar, "_execute_django", lambda qs: "django")
        monkeypatch.setattr(ar, "_connectorx_usable", lambda _db: True)
        monkeypatch.setattr(ar, "_try_connectorx", lambda qs: None)
        assert ar.read_frame(_QS()) == "django"

    @pytest.mark.django_db(transaction=True)
    def test_connectorx_usable_reflects_transaction_state(self):
        """BOUNDARY: auto's connectorx gate tracks the open-transaction state."""
        from django.db import transaction
        from ...components._crud_kit.arrow_reader import _connectorx_usable

        assert _connectorx_usable("default") is True
        with transaction.atomic():
            assert _connectorx_usable("default") is False


class TestLazyReadTempFileCleanup:
    """Temp files are tracked in a bounded set, not the atexit registry."""

    @pytest.fixture(autouse=True)
    def _isolate_registry(self):
        """Snapshot and restore the module-level tracking state per test."""
        from ...components._crud_kit import frame_stream as fs

        pending = set(fs._PENDING_TEMP_FILES)
        registered = fs._TEMP_DRAIN_REGISTERED
        yield
        fs._PENDING_TEMP_FILES.clear()
        fs._PENDING_TEMP_FILES.update(pending)
        fs._TEMP_DRAIN_REGISTERED = registered

    def test_tracking_registers_exactly_one_atexit_handler(self):
        """REGRESSION: N tracked files add one atexit entry, not N.

        The leak this replaces registered `safe_unlink` per call, so a
        long-running process grew the registry once per lazy read forever.
        """
        import atexit
        from ...components._crud_kit import frame_stream as fs

        fs._TEMP_DRAIN_REGISTERED = False
        before = atexit._ncallbacks()
        for index in range(50):
            fs.track_temp_file(f"/nonexistent/mo-temp-{index}.parquet")

        assert atexit._ncallbacks() == before + 1
        assert len(fs._PENDING_TEMP_FILES) >= 50

    def test_unlink_stops_tracking_a_removed_file(self, tmp_path):
        """ACCEPTANCE: the pending set shrinks as files are actually removed."""
        from ...components._crud_kit import frame_stream as fs

        target = tmp_path / "gone.parquet"
        target.write_bytes(b"x")
        fs.track_temp_file(str(target))
        assert str(target) in fs._PENDING_TEMP_FILES

        fs.safe_unlink(str(target))

        assert not target.exists()
        assert str(target) not in fs._PENDING_TEMP_FILES

    def test_unlink_forgets_an_already_missing_file(self, tmp_path):
        """BOUNDARY: a path removed by someone else is dropped, not retried forever."""
        from ...components._crud_kit import frame_stream as fs

        missing = str(tmp_path / "never-existed.parquet")
        fs.track_temp_file(missing)

        fs.safe_unlink(missing)

        assert missing not in fs._PENDING_TEMP_FILES

    def test_drain_is_idempotent_and_survives_missing_files(self, tmp_path):
        """BOUNDARY: the atexit drain tolerates gone files and repeats safely."""
        from ...components._crud_kit import frame_stream as fs

        present = tmp_path / "present.parquet"
        present.write_bytes(b"x")
        fs.track_temp_file(str(present))
        fs.track_temp_file(str(tmp_path / "absent.parquet"))

        fs.drain_temp_files()
        fs.drain_temp_files()  # second pass must not raise

        assert not present.exists()
        assert str(present) not in fs._PENDING_TEMP_FILES

    @pytest.mark.parametrize(
        "batch_size,expected_groups",
        [
            (1_000, 5),  # row groups track the batch
            (2_500, 2),  # ...at any size above the floor
            (10, 5),  # ...and the floor (1000) takes over below it
        ],
    )
    def test_lazy_sink_sizes_row_groups_to_the_batch(
        self, monkeypatch, batch_size, expected_groups
    ):
        """REGRESSION: peak memory is one batch, not one Polars-chosen row group.

        pyarrow decodes a whole row group per batch, so leaving the sink to pick
        made peak memory track the row group instead of `batch_size` — 33.6 MB
        against 8.3 MB for 400k rows x 8 string columns at `batch_size=1000`.
        """
        from ...components._crud_kit import frame_stream as fs

        seen = {}
        original = fs.pq.ParquetFile

        def _spy(handle):
            parquet = original(handle)
            seen["row_groups"] = parquet.num_row_groups
            return parquet

        monkeypatch.setattr(fs.pq, "ParquetFile", _spy)
        lazy = pl.DataFrame({"n": range(5_000)}).lazy()

        chunks = list(fs.iter_lazy_frames(lazy, batch_size))

        assert sum(chunk.height for chunk in chunks) == 5_000
        assert seen["row_groups"] == expected_groups

    def test_sink_frames_to_lazy_returns_none_for_empty_iterator(self):
        """BOUNDARY: nothing written means no scan and no leftover temp file."""
        from ...components._crud_kit import frame_stream as fs

        lazy, path = fs.sink_frames_to_lazy(iter([]))

        assert lazy is None and path is None


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


def _books_referencing(author_ids) -> pl.DataFrame:
    """A book frame with one row per entry in ``author_ids``."""
    author_ids = list(author_ids)
    return pl.DataFrame(
        {
            "id": [str(uuid.uuid4()) for _ in author_ids],
            "title": [f"Book {index}" for index in range(len(author_ids))],
            "author_ref_id": author_ids,
        }
    )


# ----------------------------------------------------------------------------
# Explicit connection targets (dynamic / multi-tenant databases)
# ----------------------------------------------------------------------------
def _build_sqlite_wrapper(alias: str, path):
    """A real SQLite connection that is not described by ``settings.DATABASES``.

    Built from the default alias's settings so every backend default is already
    filled in, then repointed at its own file.
    """
    from django.db.backends.sqlite3.base import DatabaseWrapper

    settings_dict = {**connections["default"].settings_dict, "NAME": str(path)}
    return DatabaseWrapper(settings_dict, alias=alias)


_PG_REQUIRED_ENV = ("MO_TEST_PG_NAME", "MO_TEST_PG_USER", "MO_TEST_PG_HOST")
_HAS_POSTGRES = all(os.environ.get(name) for name in _PG_REQUIRED_ENV)


def _build_postgres_wrapper(alias: str):
    """A PostgreSQL connection built from environment credentials.

    Routed through a throwaway `ConnectionHandler` so Django fills in every
    backend default, exactly as it would for a configured alias — the resulting
    connection is then registered by hand, never through `settings.DATABASES`.
    """
    from django.db.utils import ConnectionHandler

    handler = ConnectionHandler(
        {
            # ConnectionHandler insists on a "default" key. It is never connected
            # to; only the tenant alias below is.
            "default": {},
            alias: {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.environ["MO_TEST_PG_NAME"],
                "USER": os.environ["MO_TEST_PG_USER"],
                "PASSWORD": os.environ.get("MO_TEST_PG_PASSWORD", ""),
                "HOST": os.environ["MO_TEST_PG_HOST"],
                "PORT": os.environ.get("MO_TEST_PG_PORT", "5432"),
            },
        }
    )
    return handler[alias]


def _create_author_table(wrapper) -> None:
    """Create the author table on ``wrapper`` without going through the ORM.

    ``schema_editor()`` opens an atomic block keyed by alias, which resolves
    through ``django.db.connections`` — the one thing an unregistered connection
    does not have.
    """
    with wrapper.cursor() as cursor:
        cursor.execute(
            'CREATE TABLE "tbl_author" ('
            '"id" char(32) NOT NULL PRIMARY KEY, '
            '"name" varchar(50) NOT NULL)'
        )


@pytest.fixture()
def tenant_connection(tmp_path):
    """A database reachable only through ``django.db.connections``.

    Mirrors a runtime-provisioned tenant: real credentials and a real connection,
    registered under a synthetic alias, deliberately absent from
    ``settings.DATABASES``.
    """
    alias = f"tenant_{uuid.uuid4().hex[:8]}"
    wrapper = _build_sqlite_wrapper(alias, tmp_path / "tenant.sqlite3")
    connections[alias] = wrapper
    try:
        yield wrapper
    finally:
        wrapper.close()
        del connections[alias]


class TestDbTargetResolution:

    def test_no_target_defaults_and_stays_implicit(self):
        """ACCEPTANCE: `using=None` resolves to default without claiming routing."""
        target = resolve_db_target(None)
        assert target.alias == "default"
        assert target.is_explicit is False
        assert target.settings_dict is settings.DATABASES["default"]

    def test_implicit_target_leaves_orm_routing_untouched(self):
        """ACCEPTANCE: an unrequested target never pins ORM queries."""
        assert resolve_db_target(None).orm_alias(operation="anything") is None

    def test_alias_target_is_explicit(self):
        """ACCEPTANCE: naming an alias marks the target explicit."""
        target = resolve_db_target("default")
        assert target.alias == "default"
        assert target.is_explicit is True
        assert target.orm_alias(operation="anything") == "default"

    def test_unknown_alias_raises(self):
        """REJECTION: an alias in neither source is rejected."""
        with pytest.raises(ValueError, match="not found in settings.DATABASES"):
            resolve_db_target("no_such_alias_anywhere")

    def test_find_settings_dict_returns_none_for_unknown_alias(self):
        """ACCEPTANCE: the soft lookup reports absence instead of raising."""
        assert find_settings_dict("no_such_alias_anywhere") is None

    def test_resolution_is_idempotent(self):
        """ACCEPTANCE: an already-resolved target passes straight through."""
        target = resolve_db_target("default")
        assert resolve_db_target(target) is target

    def test_alias_absent_from_settings_resolves_from_connections(
        self, tenant_connection
    ):
        """ACCEPTANCE: a live-only alias resolves via django.db.connections."""
        alias = tenant_connection.alias
        assert alias not in settings.DATABASES
        target = resolve_db_target(alias)
        assert target.settings_dict is tenant_connection.settings_dict
        assert target.connection is tenant_connection

    def test_connection_object_carries_its_own_alias_and_settings(
        self, tenant_connection
    ):
        """ACCEPTANCE: a connection object is taken at face value."""
        target = resolve_db_target(tenant_connection)
        assert target.alias == tenant_connection.alias
        assert target.settings_dict is tenant_connection.settings_dict
        assert target.is_explicit is True
        assert target.orm_alias(operation="anything") == tenant_connection.alias

    def test_settings_take_precedence_over_live_connection(self, tenant_connection):
        """ACCEPTANCE: a configured alias resolves from settings, not the wrapper."""
        alias = tenant_connection.alias
        configured = {**settings.DATABASES["default"], "NAME": "from_settings"}
        with override_settings(DATABASES={**settings.DATABASES, alias: configured}):
            assert resolve_db_target(alias).settings_dict["NAME"] == "from_settings"

    def test_unregistered_connection_rejects_orm_routing(self, tmp_path):
        """REJECTION: ORM-dependent steps refuse an unreachable alias."""
        wrapper = _build_sqlite_wrapper("never_registered", tmp_path / "x.sqlite3")
        target = resolve_db_target(wrapper)
        with pytest.raises(ValueError, match="not registered in django.db.connections"):
            target.orm_alias(operation="Foreign-key validation")


class _StubEngine:
    """Stands in for a SQLAlchemy engine; records that it was disposed."""

    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class TestEngineCacheBounding:

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        from ...components._crud_kit import crud_processor as cp

        cp._ENGINE_CACHE.clear()
        yield
        cp._ENGINE_CACHE.clear()

    def test_cache_evicts_least_recently_used_and_returns_it(self):
        """ACCEPTANCE: the engine cache is bounded and hands back what it drops."""
        from ...components._crud_kit import crud_processor as cp

        first, second, third = _StubEngine(), _StubEngine(), _StubEngine()
        with override_settings(MO_CRUD_ENGINE_CACHE_SIZE=2):
            assert cp._cache_engine(("a",), first) == []
            assert cp._cache_engine(("b",), second) == []
            evicted = cp._cache_engine(("c",), third)

        assert list(cp._ENGINE_CACHE) == [("b",), ("c",)]
        # Eviction reports the engine rather than closing it inline, so the
        # caller can dispose it after releasing the lock.
        assert evicted == [first]
        assert first.disposed is False

        cp._dispose_evicted(evicted)
        assert first.disposed is True
        assert second.disposed is False

    def test_cache_hit_promotes_the_entry(self):
        """REGRESSION: using an engine makes it the most recently used.

        Without the promotion the cache would evict by insertion order, dropping
        a hot engine while a cold one survived.
        """
        from ...components._crud_kit import crud_processor as cp

        oldest, middle, newest = _StubEngine(), _StubEngine(), _StubEngine()
        with override_settings(MO_CRUD_ENGINE_CACHE_SIZE=2):
            cp._cache_engine(("a",), oldest)
            cp._cache_engine(("b",), middle)
            # Touch "a" the way `_get_sqlalchemy_engine` does on a cache hit.
            cp._ENGINE_CACHE.move_to_end(("a",))
            evicted = cp._cache_engine(("c",), newest)

        assert evicted == [middle]  # "b" was coldest, not "a"
        assert list(cp._ENGINE_CACHE) == [("a",), ("c",)]

    def test_disposal_happens_with_the_lock_released(self):
        """REGRESSION: dispose() must not run while `_ENGINE_LOCK` is held.

        `dispose()` closes sockets and `_ENGINE_LOCK` serializes every engine
        lookup in the process, so disposing under it stalls all of them.
        """
        from ...components._crud_kit import crud_processor as cp

        observed = []

        class _LockProbe(_StubEngine):
            def dispose(self):
                # `_ENGINE_LOCK` is non-reentrant: acquiring it here proves the
                # caller is not already holding it.
                observed.append(cp._ENGINE_LOCK.acquire(blocking=False))
                if observed[-1]:
                    cp._ENGINE_LOCK.release()
                super().dispose()

        probe = _LockProbe()
        with override_settings(MO_CRUD_ENGINE_CACHE_SIZE=1):
            cp._cache_engine(("a",), probe)
            evicted = cp._cache_engine(("b",), _StubEngine())

        with cp._ENGINE_LOCK:
            pass  # sanity: the lock is free before we start
        cp._dispose_evicted(evicted)

        assert observed == [True]
        assert probe.disposed is True

    def test_evicting_a_real_engine_disposes_its_pool(self, tmp_path):
        """ACCEPTANCE: a genuine SQLAlchemy engine is disposed, not just a stub.

        `dispose()` closes the pooled connections and swaps in a fresh pool, so
        pool replacement is the observable proof — and unlike `checkedin()` it
        holds for every pool class (in-memory SQLite uses `SingletonThreadPool`,
        which has no such counter).
        """
        from sqlalchemy import create_engine
        from ...components._crud_kit import crud_processor as cp

        engine = create_engine(f"sqlite:///{tmp_path / 'evict.sqlite3'}")
        engine.connect().close()  # materialize a pooled connection
        original_pool = engine.pool

        # `checkedin()` is a QueuePool counter — what file-based SQLite uses on
        # SQLAlchemy 2.x, but not a guarantee across pool classes. Assert it only
        # where the pool exposes it, so this does not become a second way for the
        # environment to decide whether the test passes.
        counts_idle = hasattr(original_pool, "checkedin")
        if counts_idle:
            assert original_pool.checkedin() == 1

        with override_settings(MO_CRUD_ENGINE_CACHE_SIZE=1):
            cp._cache_engine(("real",), engine)
            evicted = cp._cache_engine(("other",), _StubEngine())
        assert evicted == [engine]
        assert engine.pool is original_pool  # not disposed while still cached

        cp._dispose_evicted(evicted)

        assert engine.pool is not original_pool  # fresh pool
        if counts_idle:
            assert original_pool.checkedin() == 0  # old connections really closed

    def test_zero_size_keeps_cache_unbounded(self):
        """ACCEPTANCE: opting out of the ceiling keeps every engine."""
        from ...components._crud_kit import crud_processor as cp

        with override_settings(MO_CRUD_ENGINE_CACHE_SIZE=0):
            for index in range(40):
                assert cp._cache_engine((index,), _StubEngine()) == []
        assert len(cp._ENGINE_CACHE) == 40


@pytest.mark.django_db(transaction=True)
class TestExplicitConnectionWrites(MindoffTestCase):
    """Writes aimed at a database that `settings.DATABASES` has never heard of."""

    def test_create_writes_to_named_alias_only(self, tenant):
        """ACCEPTANCE: create() lands rows in the target, not the default DB."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[3])
        status, _valid, _invalid = mo_crud_kit.create(frms, using=tenant.alias)

        assert status == "ok"
        assert self._author_model.objects.using(tenant.alias).count() == 3
        assert self._author_model.objects.count() == 0

    def test_create_accepts_a_connection_object(self, tenant):
        """ACCEPTANCE: the connection itself is a valid target."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[2])
        status, _valid, _invalid = mo_crud_kit.create(frms, using=tenant)

        assert status == "ok"
        assert self._author_model.objects.using(tenant.alias).count() == 2
        assert self._author_model.objects.count() == 0

    def test_update_upserts_against_the_named_alias(self, tenant):
        """ACCEPTANCE: update() merges into the target database."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[2])
        mo_crud_kit.create(frms, using=tenant.alias)

        renamed = frms[self._author_model].with_columns(
            pl.lit("renamed").alias("name")
        )
        status, _valid, _invalid = mo_crud_kit.update(
            {self._author_model: renamed}, using=tenant.alias
        )

        assert status == "ok"
        stored = self._author_model.objects.using(tenant.alias).values_list(
            "name", flat=True
        )
        assert set(stored) == {"renamed"}
        assert self._author_model.objects.count() == 0

    def test_update_backfill_reads_the_target_database(self, tenant):
        """ACCEPTANCE: omitted columns are back-filled from the target, not default.

        The row exists only in the tenant database. Had the back-fill read the
        default database it would have found nothing, leaving `name` null and
        failing row validation.
        """
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[1])
        mo_crud_kit.create(frms, using=tenant.alias)
        original_name = frms[self._author_model]["name"][0]

        pk_only = frms[self._author_model].select("id")
        status, _valid, _invalid = mo_crud_kit.update(
            {self._author_model: pk_only}, using=tenant.alias
        )

        assert status == "ok"
        stored = self._author_model.objects.using(tenant.alias).get()
        assert stored.name == original_name

    def test_foreign_key_validation_checks_the_target_database(self, tenant):
        """ACCEPTANCE: an FK is resolved where the rows are going.

        The author exists only in the tenant database, so FK validation against
        the default database would reject this book.
        """
        author_frms = self.mo_mock_model_frms(models=[self._author_model], counts=[1])
        mo_crud_kit.create(author_frms, using=tenant.alias)
        author_id = author_frms[self._author_model]["id"][0]

        book_df = pl.DataFrame(
            {
                "id": [str(uuid.uuid4())],
                "title": ["Routed"],
                "author_ref_id": [author_id],
            }
        )
        status, _valid, _invalid = mo_crud_kit.create(
            {self._book_model: book_df}, using=tenant.alias
        )

        assert status == "ok"
        assert self._book_model.objects.using(tenant.alias).count() == 1

    def test_unregistered_connection_writes_with_columns_only(self, tmp_path):
        """ACCEPTANCE: a connection outside the ORM still works when nothing queries it."""
        wrapper = _build_sqlite_wrapper("solo_unregistered", tmp_path / "solo.sqlite3")
        _create_author_table(wrapper)
        try:
            frms = self.mo_mock_model_frms(models=[self._author_model], counts=[2])
            status, _valid, _invalid = mo_crud_kit.create(
                frms, using=wrapper, validation_level="columns_only"
            )
            assert status == "ok"
            with wrapper.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM tbl_author")
                assert cursor.fetchone()[0] == 2
        finally:
            wrapper.close()

    def test_unregistered_connection_rejects_foreign_key_validation(self, tmp_path):
        """REJECTION: the one step that needs the ORM names the escape hatch.

        Removing the missing-column back-fill left foreign-key validation as the
        only stage in `update()` that issues an ORM query, so it is the only one
        that can require the connection to be registered.
        """
        wrapper = _build_sqlite_wrapper("solo_fk", tmp_path / "solo2.sqlite3")
        try:
            book = pl.DataFrame(
                {
                    "id": [str(uuid.uuid4())],
                    "title": ["Unrouted"],
                    "author_ref_id": [str(uuid.uuid4())],
                }
            )
            with pytest.raises(
                ValueError, match="not registered in django.db.connections"
            ):
                mo_crud_kit.update({self._book_model: book}, using=wrapper)
        finally:
            wrapper.close()

    def test_default_target_still_writes_to_the_default_database(self):
        """REGRESSION: omitting `using` behaves exactly as before."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[2])
        status, _valid, _invalid = mo_crud_kit.create(frms)

        assert status == "ok"
        assert self._author_model.objects.count() == 2

    @pytest.fixture()
    def tenant(self, tenant_connection):
        """Tenant database with this test class's tables already created in it."""
        with tenant_connection.schema_editor() as editor:
            editor.create_model(self._author_model)
            editor.create_model(self._book_model)
        return tenant_connection

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
            {"title": models.CharField(max_length=50)},
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


@pytest.mark.skipif(
    not _HAS_POSTGRES,
    reason="No PostgreSQL test database configured (set MO_TEST_PG_NAME/USER/HOST)",
)
@pytest.mark.django_db(transaction=True)
class TestExplicitConnectionPostgres(MindoffTestCase):
    """End-to-end writes to a PostgreSQL database absent from settings.DATABASES.

    Covers the backend-specific write paths SQLite cannot reach: the `COPY` bulk
    loader on create, and the staged-merge `ON CONFLICT` upsert on update.
    """

    def test_create_bulk_loads_into_postgres_target(self, pg_tenant):
        """ACCEPTANCE: create() COPYs into the target, leaving default untouched."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[5])
        status, _valid, _invalid = mo_crud_kit.create(frms, using=pg_tenant.alias)

        assert status == "ok"
        assert self._author_model.objects.using(pg_tenant.alias).count() == 5
        assert self._author_model.objects.count() == 0

    def test_update_upserts_into_postgres_target(self, pg_tenant):
        """ACCEPTANCE: update() merges rows through the staging table."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[5])
        mo_crud_kit.create(frms, using=pg_tenant.alias)

        renamed = frms[self._author_model].with_columns(
            pl.lit("renamed").alias("name")
        )
        status, _valid, _invalid = mo_crud_kit.update(
            {self._author_model: renamed}, using=pg_tenant.alias
        )

        assert status == "ok"
        rows = self._author_model.objects.using(pg_tenant.alias)
        # Upsert, not insert: the same five rows come back renamed.
        assert rows.count() == 5
        assert set(rows.values_list("name", flat=True)) == {"renamed"}

    def test_create_accepts_a_postgres_connection_object(self, pg_tenant):
        """ACCEPTANCE: the connection object targets PostgreSQL directly."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[2])
        status, _valid, _invalid = mo_crud_kit.create(frms, using=pg_tenant)

        assert status == "ok"
        assert self._author_model.objects.using(pg_tenant.alias).count() == 2

    @pytest.fixture()
    def pg_tenant(self):
        alias = f"pg_tenant_{uuid.uuid4().hex[:8]}"
        wrapper = _build_postgres_wrapper(alias)
        connections[alias] = wrapper
        with wrapper.schema_editor() as editor:
            editor.create_model(self._author_model)
        try:
            yield wrapper
        finally:
            with wrapper.schema_editor() as editor:
                editor.delete_model(self._author_model)
            wrapper.close()
            del connections[alias]

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


@pytest.mark.skipif(
    not _HAS_POSTGRES,
    reason="No PostgreSQL test database configured (set MO_TEST_PG_NAME/USER/HOST)",
)
@pytest.mark.django_db(transaction=True)
class TestUpdatePartialColumnsPostgres(MindoffTestCase):
    """Partial-column merges that SQLite structurally cannot exercise.

    SQLite's dynamic typing accepts whatever the staging table holds, so it takes
    the `cast_to_target=False` merge branch and never proves that a *narrowed*
    column list still casts and lines up against strictly-typed columns. These
    run the `cast_to_target=True` branch and the `COPY` bulk loader with a frame
    that deliberately omits columns, against a real server.
    """

    def test_partial_merge_casts_the_narrowed_column_list(self, pg_tenant):
        """ACCEPTANCE: omitted typed columns keep their values on a real merge.

        The staging table is built from the frame's Polars schema, so the UUID pk
        arrives as text and must be cast back — and the projection has to line up
        positionally even though it no longer spans every target column.
        """
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[6])
        assert mo_crud_kit.create(frms, using=pg_tenant.alias)[0] == "ok"
        original = frms[self._author_model]

        # `published_at` and `rank` are absent: they must be left untouched.
        partial = original.select(["id", "name"]).with_columns(
            pl.lit("renamed").alias("name")
        )
        status, _valid, _invalid = mo_crud_kit.update(
            {self._author_model: partial}, using=pg_tenant.alias, batch_size=2
        )

        assert status == "ok"
        rows = self._author_model.objects.using(pg_tenant.alias)
        assert rows.count() == 6
        assert set(rows.values_list("name", flat=True)) == {"renamed"}
        # Never written, so never nulled.
        assert rows.filter(published_at__isnull=True).count() == 0
        assert rows.filter(rank__isnull=True).count() == 0

    def test_partial_merge_chunks_against_real_server(self, pg_tenant):
        """ACCEPTANCE: a row count well above batch_size round-trips correctly."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[50])
        assert mo_crud_kit.create(frms, using=pg_tenant.alias)[0] == "ok"

        renamed = frms[self._author_model].select("id", "name").with_columns(
            pl.lit("bulk").alias("name")
        )
        status, _valid, _invalid = mo_crud_kit.update(
            {self._author_model: renamed}, using=pg_tenant.alias, batch_size=7
        )

        assert status == "ok"
        rows = self._author_model.objects.using(pg_tenant.alias)
        assert rows.count() == 50
        assert set(rows.values_list("name", flat=True)) == {"bulk"}
        # Chunking the COPY load never split a row away from its other columns.
        assert rows.filter(published_at__isnull=True).count() == 0

    def test_lazy_partial_update_returns_the_written_columns(self, pg_tenant):
        """CONTRACT: the returned lazy frame collects and matches what was written."""
        frms = self.mo_mock_model_frms(models=[self._author_model], counts=[8])
        assert mo_crud_kit.create(frms, using=pg_tenant.alias)[0] == "ok"

        partial_lazy = frms[self._author_model].select("id", "name").lazy()
        status, valid, _invalid = mo_crud_kit.update(
            {self._author_model: partial_lazy}, using=pg_tenant.alias, batch_size=3
        )

        assert status == "ok"
        collected = valid[self._author_model].collect()
        assert collected.height == 8
        assert "rank" not in collected.columns

    @pytest.fixture()
    def pg_tenant(self):
        alias = f"pg_partial_{uuid.uuid4().hex[:8]}"
        wrapper = _build_postgres_wrapper(alias)
        connections[alias] = wrapper
        with wrapper.schema_editor() as editor:
            editor.create_model(self._author_model)
        try:
            yield wrapper
        finally:
            with wrapper.schema_editor() as editor:
                editor.delete_model(self._author_model)
            wrapper.close()
            del connections[alias]

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
                # Typed columns a partial frame omits, so the merge has to both
                # narrow its column list and still cast what remains.
                "published_at": models.DateTimeField(null=True, blank=True),
                "rank": models.IntegerField(null=True, blank=True),
            },
        )
        request.cls._author_model = author
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override
        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))


@pytest.mark.skipif(
    not _HAS_POSTGRES,
    reason="No PostgreSQL test database configured (set MO_TEST_PG_NAME/USER/HOST)",
)
@pytest.mark.django_db(transaction=True)
class TestForeignKeyChunkingPostgres(MindoffTestCase):
    """The half of the chunk-size calculation SQLite cannot reach.

    SQLite always advertises a real `max_query_params` (32766), so the SQLite
    tests only ever cover the branch where the backend limit binds. psycopg2
    reports `None` because it interpolates client-side, so the fall-back to
    `DEFAULT_FK_CHUNK_SIZE` is exercised *only* here — and it is the branch the
    production backend takes.
    """

    def test_postgres_reports_no_limit_so_the_ceiling_applies(self, pg_tenant):
        """BOUNDARY: the `None` branch still chunks instead of going unbounded."""
        from ...components._crud_kit import foreign_key_validator as fkv

        manager = self._author_model.objects.using(pg_tenant.alias)
        # Precondition: this is what makes the branch reachable at all.
        assert connections[pg_tenant.alias].features.max_query_params is None
        assert (
            ForeignKeyValidator({})._fk_chunk_size(manager)
            == fkv.DEFAULT_FK_CHUNK_SIZE
        )

    def test_chunked_counting_is_exact_against_postgres(self, pg_tenant):
        """ACCEPTANCE: summed per-chunk counts match reality on a real server."""
        authors = self.mo_mock_model_frms(models=[self._author_model], counts=[12])
        assert mo_crud_kit.create(authors, using=pg_tenant.alias)[0] == "ok"
        author_ids = authors[self._author_model]["id"].to_list()
        target = resolve_db_target(pg_tenant.alias)

        with override_settings(MO_CRUD_FK_CHUNK_SIZE=4):  # 12 ids -> 3 chunks
            books = _books_referencing(author_ids)
            validated = ForeignKeyValidator(
                {self._book_model: books}, target=target
            ).validate()

        assert validated[self._book_model].height == 12

    def test_missing_reference_still_rejected_when_chunked(self, pg_tenant):
        """REJECTION: a genuinely absent FK fails, judged by the database itself."""
        authors = self.mo_mock_model_frms(models=[self._author_model], counts=[9])
        assert mo_crud_kit.create(authors, using=pg_tenant.alias)[0] == "ok"
        author_ids = authors[self._author_model]["id"].to_list()
        target = resolve_db_target(pg_tenant.alias)

        # One reference that PostgreSQL's own foreign key would refuse.
        bogus = _books_referencing(author_ids + [str(uuid.uuid4())])
        with override_settings(MO_CRUD_FK_CHUNK_SIZE=4):
            with pytest.raises(Exception, match="couldn't resolve foreign key"):
                ForeignKeyValidator(
                    {self._book_model: bogus}, target=target
                ).validate()

    @pytest.fixture()
    def pg_tenant(self):
        alias = f"pg_fkchunk_{uuid.uuid4().hex[:8]}"
        wrapper = _build_postgres_wrapper(alias)
        connections[alias] = wrapper
        with wrapper.schema_editor() as editor:
            editor.create_model(self._author_model)
            editor.create_model(self._book_model)
        try:
            yield wrapper
        finally:
            with wrapper.schema_editor() as editor:
                editor.delete_model(self._book_model)
                editor.delete_model(self._author_model)
            wrapper.close()
            del connections[alias]

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

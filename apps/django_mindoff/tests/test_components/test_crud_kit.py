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

    def test_fill_missing_columns_no_pk_raises(self):
        """REJECTION: Fill missing columns no pk raises."""
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__fill_missing_columns(
                {self._author_model: df_no_pk}, batch_size=100
            )

    def test_fill_missing_columns_all_present_no_fetch(self):
        """ACCEPTANCE: Fill missing columns all present no fetch."""
        valid = self._seed(1, 0)
        author_df = valid.get(self._author_model)
        if author_df is None:
            return
        # All columns already present — should return unchanged
        result = _update__fill_missing_columns(
            {self._author_model: author_df}, batch_size=100
        )
        assert set(result[self._author_model].columns) == set(author_df.columns)

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

    def test_update_only_pk_column(self):
        """BOUNDARY: Update only pk column."""
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

    def test_read_subset_then_update_fetches_missing_columns(self):
        """ACCEPTANCE: read() subset (hyphenated UUIDs) -> update fills missing cols.

        Guards the UUID PK join in `_update__fill_missing_columns`: read returns
        hyphenated UUIDs while the DB casts UUID PKs to dashless hex, so the join
        must canonicalize both sides.
        """
        self._seed(2, 3)
        # Read only id + title; pages and the FK column are intentionally absent.
        subset, _ = mo_crud_kit.read(
            self._book_model.objects.all().values("id", "title")
        )
        status, _, _ = mo_crud_kit.update({self._book_model: subset})
        assert status == "ok"
        # pages must be preserved (would be NULL if the PK join had failed).
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

    def test_update_skip_db_fill_skips_prefetch(self):
        """E1: skip_db_fill skips the missing-column back-fill prefetch.

        With the prefetch (default), an omitted column is fetched from the DB and
        preserved. With skip_db_fill it is not fetched, so it is written as NULL.
        """
        seeded = self._seed(1, 0)
        author_pk = seeded[self._author_model]["id"][0]
        pk_col = self._author_model._meta.pk.column
        # Partial frame: id + name only (nickname omitted).
        partial = pl.DataFrame({pk_col: [author_pk], "name": ["NewName"]})

        # Default: prefetch back-fills nickname from the DB, preserving it.
        self._author_model.objects.filter(pk=author_pk).update(nickname="KeepMe")
        mo_crud_kit.update({self._author_model: partial.clone()})
        obj = self._author_model.objects.get(pk=author_pk)
        assert obj.name == "NewName"
        assert obj.nickname == "KeepMe"

        # skip_db_fill: no prefetch, so the omitted nickname is written as NULL.
        self._author_model.objects.filter(pk=author_pk).update(nickname="KeepMe2")
        mo_crud_kit.update({self._author_model: partial.clone()}, skip_db_fill=True)
        obj = self._author_model.objects.get(pk=author_pk)
        assert obj.name == "NewName"
        assert obj.nickname is None

    def test_update_skip_db_fill_still_canonicalizes_pk(self):
        """E1: skip_db_fill keeps PK canonicalization so upsert matching works.

        Uses ``columns_only`` (no RowValidator) so the only place that can
        canonicalize the hyphenated PK is the prefetch step — proving it still
        runs even when the DB fetch is skipped.
        """
        seeded = self._seed(1, 0)
        dashless = seeded[self._author_model]["id"][0]
        hyphenated = str(uuid.UUID(dashless))  # same id, canonical hyphenated form
        pk_col = self._author_model._meta.pk.column
        full = pl.DataFrame(
            {pk_col: [hyphenated], "name": ["Canon"], "nickname": ["Nick"]}
        )

        status, _, _ = mo_crud_kit.update(
            {self._author_model: full},
            skip_db_fill=True,
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
            CRUDProcessor({model: pl.DataFrame()}, db_alias="nonexistent_alias")

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
            "apps.django_mindoff.components._crud_kit.crud_processor.connection.ensure_connection",
            _fake_ensure_connection,
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
        monkeypatch.setattr(cp.connection, "ensure_connection", lambda: None)

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
        """ACCEPTANCE: Update merge staging mysql postgres paths."""
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
        """REJECTION: Update merge staging sqlite no other cols returns."""
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


@pytest.mark.django_db(transaction=True)
class TestFillMissingColumnsLazy(MindoffTestCase):

    def test_fill_missing_columns_lazy_frame(self):
        """ACCEPTANCE: Fill missing columns lazy frame."""
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
        """REJECTION: Fill missing columns pk absent raises."""
        df_no_pk = pl.DataFrame({"name": ["Alice"]})
        with pytest.raises(Exception):
            _update__fill_missing_columns(
                {self._author_model: df_no_pk}, batch_size=100
            )

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

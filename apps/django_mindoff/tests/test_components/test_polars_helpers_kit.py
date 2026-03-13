from __future__ import annotations
from types import SimpleNamespace
from pathlib import Path
from typing import Literal, Union
from unittest.mock import MagicMock
import polars as pl
import pytest
from ...components.polars_kit import MindoffPolarsKit, mo_polars_kit
from ...components._helper_kit.file_guardian import file_guardian
from ...components._helper_kit import file_guardian as file_guardian_mod
from ...components._helper_kit.validate_schema import validate_schema
from ...components._polars_kit.json_to_frame import (
    MODEL_FRMS_DEFAULT_ROOT_KEY,
    PayloadFlattener,
    build_model_frms,
    json_to_frame,
)
from ...components.helper_kit import (
    pascal_to_snake,
    mo_helper_kit,
)
from ...components.validation_kit import MindoffValidationError


kit = MindoffPolarsKit()


class TestIsFrmEmpty:

    def test_empty_dataframe_returns_true(self):
        """ACCEPTANCE: Empty DataFrame is recognized as empty."""
        assert kit.is_frm_empty(pl.DataFrame()) is True

    def test_non_empty_dataframe_returns_false(self):
        """ACCEPTANCE: DataFrame with data is not empty."""
        assert kit.is_frm_empty(_df(("x", [1]))) is False

    def test_empty_lazyframe_returns_true(self):
        """ACCEPTANCE: Empty LazyFrame is recognized as empty."""
        assert kit.is_frm_empty(pl.DataFrame().lazy()) is True

    def test_non_empty_lazyframe_returns_false(self):
        """ACCEPTANCE: LazyFrame with data is not empty."""
        assert kit.is_frm_empty(_lf(("x", [1]))) is False

    def test_lazyframe_with_schema_but_zero_rows_returns_true(self):
        """BOUNDARY: LazyFrame with schema but zero rows is treated as empty."""
        lf = pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)}).lazy()
        assert kit.is_frm_empty(lf) is True

    def test_lazyframe_with_no_schema_returns_true(self):
        """BOUNDARY: LazyFrame with no columns or schema is treated as empty."""
        lf = pl.DataFrame({}).lazy()
        assert kit.is_frm_empty(lf) is True


class TestIsModelFrmsEmpty:

    def test_all_empty_returns_true(self):
        """ACCEPTANCE: Dict with all empty frames returns True."""
        frms = {_M1: pl.DataFrame(), _M2: pl.DataFrame()}
        assert kit.is_model_frms_empty(frms) is True

    def test_one_non_empty_returns_false(self):
        """ACCEPTANCE: Dict with at least one non-empty frame returns False."""
        frms = {_M1: pl.DataFrame(), _M2: _df(("a", [1]))}
        assert kit.is_model_frms_empty(frms) is False

    def test_all_non_empty_returns_false(self):
        """ACCEPTANCE: Dict where all frames have data returns False."""
        frms = {_M1: _df(("a", [1])), _M2: _df(("b", [2]))}
        assert kit.is_model_frms_empty(frms) is False

    def test_empty_dict_returns_true(self):
        """ANOMALY: Empty dict returns True because all() on empty iterable is True."""
        assert kit.is_model_frms_empty({}) is True

    def test_mixed_lazy_and_eager_all_empty_returns_true(self):
        """ANOMALY: Mix of empty LazyFrame and empty DataFrame is still considered fully empty."""
        frms = {_M1: pl.DataFrame().lazy(), _M2: pl.DataFrame()}
        assert kit.is_model_frms_empty(frms) is True


class TestIsModelFrmsNotEmpty:

    def test_all_empty_returns_false(self):
        """ACCEPTANCE: All empty frames means none are non-empty."""
        assert kit.is_model_frms_not_empty({_M1: pl.DataFrame()}) is False

    def test_one_non_empty_returns_true(self):
        """ACCEPTANCE: At least one non-empty frame returns True."""
        assert kit.is_model_frms_not_empty({_M1: _df(("a", [1]))}) is True

    def test_all_non_empty_returns_true(self):
        """ACCEPTANCE: All frames having data returns True."""
        frms = {_M1: _df(("a", [1])), _M2: _df(("b", [2]))}
        assert kit.is_model_frms_not_empty(frms) is True

    def test_empty_dict_returns_false(self):
        """ANOMALY: Empty dict returns False because any() on empty iterable is False."""
        assert kit.is_model_frms_not_empty({}) is False


class TestCollectModelFrms:

    def test_lazyframe_is_collected_to_dataframe(self):
        """ACCEPTANCE: LazyFrame in dict is collected into a DataFrame."""
        frms = {_M1: _lf(("a", [1, 2]))}
        result = kit.collect_model_frms(frms)
        assert isinstance(result[_M1], pl.DataFrame)
        assert result[_M1].height == 2

    def test_dataframe_passes_through_unchanged(self):
        """ACCEPTANCE: DataFrame in dict passes through collect unchanged."""
        df = _df(("a", [1]))
        frms = {_M1: df}
        result = kit.collect_model_frms(frms)
        assert isinstance(result[_M1], pl.DataFrame)
        assert result[_M1].equals(df)

    def test_mixed_dict_collects_only_lazy(self):
        """ACCEPTANCE: Mixed dict collects LazyFrames while DataFrames remain as-is."""
        df = _df(("a", [1]))
        lf = _lf(("b", [2, 3]))
        frms = {_M1: df, _M2: lf}
        result = kit.collect_model_frms(frms)
        assert isinstance(result[_M1], pl.DataFrame)
        assert isinstance(result[_M2], pl.DataFrame)
        assert result[_M2].height == 2

    def test_streaming_false_is_forwarded(self):
        """BOUNDARY: streaming=False is accepted and still returns a DataFrame."""
        frms = {_M1: _lf(("a", [1]))}
        result = kit.collect_model_frms(frms, streaming=False)
        assert isinstance(result[_M1], pl.DataFrame)


class TestSyncModelFrmsType:

    def test_all_dataframes_returns_same_object(self):
        """ACCEPTANCE: All-DataFrame dict is returned as the same object unchanged."""
        frms = {_M1: _df(("a", [1])), _M2: _df(("b", [2]))}
        result = kit.sync_model_frms_type(frms)
        assert result is frms  # identity — untouched

    def test_any_lazyframe_converts_dataframes_to_lazy(self):
        """ACCEPTANCE: Presence of any LazyFrame causes all DataFrames to be converted to lazy."""
        frms = {_M1: _df(("a", [1])), _M2: _lf(("b", [2]))}
        result = kit.sync_model_frms_type(frms)
        assert isinstance(result[_M1], pl.LazyFrame)
        assert isinstance(result[_M2], pl.LazyFrame)

    def test_all_lazyframes_stays_lazy(self):
        """ACCEPTANCE: All-LazyFrame dict remains fully lazy after sync."""
        frms = {_M1: _lf(("a", [1])), _M2: _lf(("b", [2]))}
        result = kit.sync_model_frms_type(frms)
        assert all(isinstance(v, pl.LazyFrame) for v in result.values())

    def test_empty_dict_returns_unchanged(self):
        """ANOMALY: Empty dict is returned as the same object with no modification."""
        frms: dict = {}
        result = kit.sync_model_frms_type(frms)
        assert result is frms


class TestHasNullsInFrmCol:

    def test_dataframe_with_null_returns_true(self):
        """ACCEPTANCE: DataFrame column containing a null returns True."""
        df = _df(("a", [1, None]))
        assert kit.has_nulls_in_frm_col(df, "a") is True

    def test_dataframe_without_null_returns_false(self):
        """ACCEPTANCE: DataFrame column with no nulls returns False."""
        df = _df(("a", [1, 2]))
        assert kit.has_nulls_in_frm_col(df, "a") is False

    def test_lazyframe_with_null_returns_true(self):
        """ACCEPTANCE: LazyFrame column containing a null returns True."""
        lf = _lf(("a", [None, 1]))
        assert kit.has_nulls_in_frm_col(lf, "a") is True

    def test_lazyframe_without_null_returns_false(self):
        """ACCEPTANCE: LazyFrame column with no nulls returns False."""
        lf = _lf(("a", [1, 2]))
        assert kit.has_nulls_in_frm_col(lf, "a") is False

    def test_all_nulls_returns_true(self):
        """BOUNDARY: Column where every value is null returns True."""
        df = _df(("a", [None, None]))
        assert kit.has_nulls_in_frm_col(df, "a") is True

    def test_single_row_no_null_returns_false(self):
        """BOUNDARY: Single-row column with a real value returns False."""
        df = _df(("a", [42]))
        assert kit.has_nulls_in_frm_col(df, "a") is False


class TestSplitModelFrmsOnNull:

    def test_splits_correctly_when_column_present(self):
        """ACCEPTANCE: Rows with null error column go to valid, non-null go to invalid."""
        df = _df(("name", ["a", "b", "c"]), ("__error__info", [None, "err", None]))
        frms = {_M1: df}
        valid, invalid = kit.split_model_frms_on_null(frms)
        assert valid[_M1].height == 2
        assert invalid[_M1].height == 1

    def test_all_valid_when_no_errors(self):
        """ACCEPTANCE: All null error values means all rows go to valid."""
        df = _df(("name", ["a", "b"]), ("__error__info", [None, None]))
        frms = {_M1: df}
        valid, invalid = kit.split_model_frms_on_null(frms)
        assert valid[_M1].height == 2
        assert invalid[_M1].height == 0

    def test_all_invalid_when_all_errors(self):
        """ACCEPTANCE: All non-null error values means all rows go to invalid."""
        df = _df(("name", ["a", "b"]), ("__error__info", ["e1", "e2"]))
        frms = {_M1: df}
        valid, invalid = kit.split_model_frms_on_null(frms)
        assert valid[_M1].height == 0
        assert invalid[_M1].height == 2

    def test_adds_null_column_when_absent_all_go_to_valid(self):
        """ACCEPTANCE: Missing error column is auto-added as all-null, routing all rows to valid."""
        df = _df(("name", ["a", "b"]))
        frms = {_M1: df}
        valid, invalid = kit.split_model_frms_on_null(frms)
        assert valid[_M1].height == 2
        assert invalid[_M1].height == 0
        assert "__error__info" in valid[_M1].columns

    def test_custom_column_name(self):
        """BOUNDARY: A custom column name is used instead of the default error column."""
        df = _df(("v", [1, 2, 3]), ("flag", [None, "bad", None]))
        frms = {_M1: df}
        valid, invalid = kit.split_model_frms_on_null(frms, column="flag")
        assert valid[_M1].height == 2
        assert invalid[_M1].height == 1

    def test_multiple_models_split_independently(self):
        """ANOMALY: Multiple models are each split independently of one another."""
        df1 = _df(("x", [1, 2]), ("__error__info", [None, "err"]))
        df2 = _df(("x", [3, 4]), ("__error__info", ["err", None]))
        frms = {_M1: df1, _M2: df2}
        valid, invalid = kit.split_model_frms_on_null(frms)
        assert valid[_M1].height == 1
        assert invalid[_M1].height == 1
        assert valid[_M2].height == 1
        assert invalid[_M2].height == 1


class TestFrmFillNull:

    def test_lit_scalar_fills_nulls(self):
        """ACCEPTANCE: Literal scalar value fills null cells in the column."""
        df = _df(("a", [1, None, 3]))
        result = kit.frm_fill_null(df, column="a", fill_value=99, mode="lit")
        assert result["a"].to_list() == [1, 99, 3]

    def test_lit_callable_is_called_with_custom_params(self):
        """ACCEPTANCE: Callable fill_value in lit mode is called with extra kwargs to produce the fill."""
        df = _df(("a", ["x", None]))
        result = kit.frm_fill_null(
            df,
            column="a",
            fill_value=lambda prefix: f"{prefix}_val",
            mode="lit",
            prefix="test",
        )
        assert result["a"].to_list() == ["x", "test_val"]

    def test_lit_no_nulls_leaves_column_unchanged(self):
        """ACCEPTANCE: Column with no nulls is left unchanged when using lit mode."""
        df = _df(("a", [1, 2, 3]))
        result = kit.frm_fill_null(df, column="a", fill_value=0, mode="lit")
        assert result["a"].to_list() == [1, 2, 3]

    def test_map_callable_fills_nulls(self):
        """ACCEPTANCE: Callable fill_value in map mode is invoked per null row."""
        df = _df(("a", [1, None, None]))
        counter = {"n": 0}

        def _gen():
            counter["n"] += 1
            return counter["n"] * 10

        result = kit.frm_fill_null(df, column="a", fill_value=_gen, mode="map")
        vals = result["a"].to_list()
        assert vals[0] == 1
        assert vals[1] is not None
        assert vals[2] is not None

    def test_sink_map_with_lazyframe_returns_lazyframe(self):
        """ACCEPTANCE: sink_map mode on a LazyFrame returns a LazyFrame with nulls filled."""
        lf = _lf(("a", ["hello", None]))
        result = kit.frm_fill_null(
            lf, column="a", fill_value=lambda: "filled", mode="sink_map"
        )
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert "filled" in collected["a"].to_list()

    def test_sink_map_with_dataframe_returns_dataframe(self):
        """ACCEPTANCE: sink_map mode on a DataFrame still returns a DataFrame."""
        df = _df(("a", [None]))
        result = kit.frm_fill_null(
            df, column="a", fill_value=lambda: "x", mode="sink_map"
        )
        assert isinstance(result, pl.DataFrame)

    def test_non_callable_with_map_mode_raises(self):
        """REJECTION: Non-callable fill_value with map mode raises an exception."""
        df = _df(("a", [None]))
        with pytest.raises(Exception):
            kit.frm_fill_null(df, column="a", fill_value="static", mode="map")

    def test_non_callable_with_sink_map_mode_raises(self):
        """REJECTION: Non-callable fill_value with sink_map mode raises an exception."""
        df = _df(("a", [None]))
        with pytest.raises(Exception):
            kit.frm_fill_null(df, column="a", fill_value=42, mode="sink_map")

    def test_dtype_is_respected(self):
        """BOUNDARY: Specified dtype is preserved on the filled column."""
        df = _df(("a", pl.Series([None], dtype=pl.Int64)))
        result = kit.frm_fill_null(
            df, column="a", fill_value=7, mode="lit", dtype=pl.Int64
        )
        assert result["a"].dtype == pl.Int64
        assert result["a"][0] == 7

    def test_all_nulls_filled(self):
        """ANOMALY: Column that is entirely null gets all values filled."""
        df = _df(("a", [None, None]))
        result = kit.frm_fill_null(df, column="a", fill_value="v", mode="lit")
        assert result["a"].to_list() == ["v", "v"]


class TestFrmFillNotNull:

    def test_lit_scalar_replaces_non_nulls(self):
        """ACCEPTANCE: Literal scalar replaces all non-null values, leaving nulls untouched."""
        df = _df(("a", [1, None, 3]))
        result = kit.frm_fill_notnull(df, column="a", fill_value=99, mode="lit")
        vals = result["a"].to_list()
        assert vals[0] == 99
        assert vals[1] is None
        assert vals[2] == 99

    def test_lit_callable_replaces_non_nulls(self):
        """ACCEPTANCE: Callable fill_value in lit mode replaces non-null values using extra kwargs."""
        df = _df(("a", ["x", None, "y"]))
        result = kit.frm_fill_notnull(
            df,
            column="a",
            fill_value=lambda suffix: f"new_{suffix}",
            mode="lit",
            suffix="z",
        )
        vals = result["a"].to_list()
        assert vals[0] == "new_z"
        assert vals[1] is None
        assert vals[2] == "new_z"

    def test_map_callable_replaces_non_nulls(self):
        """ACCEPTANCE: Callable in map mode without row_param replaces each non-null value."""
        df = _df(("a", [1, None, 3]))
        result = kit.frm_fill_notnull(df, column="a", fill_value=lambda: 0, mode="map")
        vals = result["a"].to_list()
        assert vals[0] == 0
        assert vals[1] is None
        assert vals[2] == 0

    def test_map_with_row_param_receives_original_value(self):
        """ACCEPTANCE: Callable with row_param receives the original cell value as argument."""
        df = _df(("a", ["hello", None, "world"]))
        result = kit.frm_fill_notnull(
            df,
            column="a",
            fill_value=lambda val: val.upper(),
            mode="map",
            row_param="val",
        )
        vals = result["a"].to_list()
        assert vals[0] == "HELLO"
        assert vals[1] is None
        assert vals[2] == "WORLD"

    def test_sink_map_with_lazyframe_returns_lazyframe(self):
        """ACCEPTANCE: sink_map mode on a LazyFrame returns a LazyFrame with non-nulls replaced."""
        lf = _lf(("a", ["keep", None]))
        result = kit.frm_fill_notnull(
            lf, column="a", fill_value=lambda: "replaced", mode="sink_map"
        )
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert "replaced" in collected["a"].to_list()

    def test_sink_map_with_dataframe_returns_dataframe(self):
        """ACCEPTANCE: sink_map mode on a DataFrame still returns a DataFrame."""
        df = _df(("a", ["x"]))
        result = kit.frm_fill_notnull(
            df, column="a", fill_value=lambda: "y", mode="sink_map"
        )
        assert isinstance(result, pl.DataFrame)

    def test_row_param_with_lit_mode_raises(self):
        """REJECTION: Using row_param with lit mode raises an exception."""
        df = _df(("a", ["x"]))
        with pytest.raises(Exception):
            kit.frm_fill_notnull(
                df, column="a", fill_value="v", mode="lit", row_param="x"
            )

    def test_non_callable_with_map_mode_raises(self):
        """REJECTION: Non-callable fill_value with map mode raises an exception."""
        df = _df(("a", [1]))
        with pytest.raises(Exception):
            kit.frm_fill_notnull(df, column="a", fill_value=42, mode="map")

    def test_non_callable_with_sink_map_mode_raises(self):
        """REJECTION: Non-callable fill_value with sink_map mode raises an exception."""
        df = _df(("a", [1]))
        with pytest.raises(Exception):
            kit.frm_fill_notnull(df, column="a", fill_value=42, mode="sink_map")

    def test_all_nulls_leaves_column_unchanged(self):
        """BOUNDARY: Column that is entirely null is left unchanged when replacing non-nulls."""
        df = _df(("a", pl.Series([None, None], dtype=pl.String)))
        result = kit.frm_fill_notnull(
            df, column="a", fill_value=lambda: "x", mode="map"
        )
        assert result["a"].to_list() == [None, None]

    def test_no_nulls_all_replaced(self):
        """BOUNDARY: Column with no nulls has every value replaced."""
        df = _df(("a", [1, 2, 3]))
        result = kit.frm_fill_notnull(df, column="a", fill_value=0, mode="lit")
        assert result["a"].to_list() == [0, 0, 0]


class TestGetFrmHeight:

    def test_dataframe_height(self):
        """ACCEPTANCE: Returns correct row count for a non-empty DataFrame."""
        assert kit.get_frm_height(_df(("a", [1, 2, 3]))) == 3

    def test_lazyframe_height(self):
        """ACCEPTANCE: Returns correct row count for a non-empty LazyFrame."""
        assert kit.get_frm_height(_lf(("a", [1, 2]))) == 2

    def test_empty_dataframe_height_is_zero(self):
        """BOUNDARY: Empty DataFrame reports height of zero."""
        assert kit.get_frm_height(pl.DataFrame({"a": []})) == 0

    def test_empty_lazyframe_height_is_zero(self):
        """BOUNDARY: Empty LazyFrame reports height of zero."""
        assert kit.get_frm_height(pl.DataFrame({"a": []}).lazy()) == 0

    def test_single_row_dataframe(self):
        """BOUNDARY: Single-row DataFrame reports height of one."""
        assert kit.get_frm_height(_df(("a", [42]))) == 1


class TestMoPolarsKitEntryPoint:

    def test_entry_point_is_mindoff_polars_kit_instance(self):
        """ACCEPTANCE: mo_polars_kit entry point is an instance of MindoffPolarsKit."""
        assert isinstance(mo_polars_kit, MindoffPolarsKit)


class TestPascalToSnake:

    def test_basic_pascal_case(self):
        """ACCEPTANCE: Simple two-word PascalCase is converted correctly."""
        assert pascal_to_snake("MyModel") == "my_model"

    def test_multi_word(self):
        """ACCEPTANCE: Three-word PascalCase is converted correctly."""
        assert pascal_to_snake("MyTestModel") == "my_test_model"

    def test_single_word_lowercased(self):
        """ACCEPTANCE: Single capitalized word is lowercased."""
        assert pascal_to_snake("Model") == "model"

    def test_already_snake_case_unchanged(self):
        """BOUNDARY: Already snake_case string is returned unchanged."""
        assert pascal_to_snake("my_model") == "my_model"

    def test_all_lowercase_unchanged(self):
        """BOUNDARY: All-lowercase string is returned unchanged."""
        assert pascal_to_snake("model") == "model"

    def test_empty_string(self):
        """BOUNDARY: Empty string input returns empty string."""
        assert pascal_to_snake("") == ""

    def test_consecutive_capitals(self):
        """ANOMALY: Consecutive uppercase letters each get a leading underscore inserted."""
        result = pascal_to_snake("HTTPError")
        assert result == "h_t_t_p_error"

    def test_single_uppercase_letter(self):
        """ANOMALY: Single uppercase letter is simply lowercased."""
        assert pascal_to_snake("A") == "a"


class TestGetAppModulePath:

    def test_returns_dotted_path_for_known_app(self, monkeypatch):
        """ACCEPTANCE: Known app name returns its full dotted module path."""
        import importlib

        monkeypatch.setattr(
            "django.conf.settings",
            SimpleNamespace(INSTALLED_APPS=["myproject.apps.myapp"]),
        )
        monkeypatch.setattr(importlib, "import_module", lambda path: MagicMock())
        from ...components import helper_kit as hk

        monkeypatch.setattr(
            hk, "settings", SimpleNamespace(INSTALLED_APPS=["myproject.apps.myapp"])
        )
        monkeypatch.setattr(hk.importlib, "import_module", lambda path: MagicMock())

        result = hk.get_app_module_path("myapp")
        assert result == "myproject.apps.myapp"

    def test_raises_value_error_for_unknown_app(self, monkeypatch):
        """REJECTION: App name not in INSTALLED_APPS raises ValueError."""
        from ...components import helper_kit as hk

        monkeypatch.setattr(hk, "settings", SimpleNamespace(INSTALLED_APPS=[]))
        with pytest.raises(ValueError, match="not found"):
            hk.get_app_module_path("nonexistent")

    def test_import_error_is_skipped_tries_next(self, monkeypatch):
        """ANOMALY: If import fails for one matching path, the next matching path is tried."""
        import importlib as _il

        calls = []

        def _fake_import(path):
            calls.append(path)
            if path == "bad.myapp":
                raise ImportError("nope")
            return MagicMock()

        from ...components import helper_kit as hk

        monkeypatch.setattr(
            hk, "settings", SimpleNamespace(INSTALLED_APPS=["bad.myapp", "good.myapp"])
        )
        monkeypatch.setattr(hk.importlib, "import_module", _fake_import)
        result = hk.get_app_module_path("myapp")
        assert result == "good.myapp"


class TestGetCurrentAppName:

    def test_returns_app_label_when_frame_matches(self, monkeypatch):
        """ACCEPTANCE: Returns the app label when a matching frame is found in the call stack."""
        from django.apps import apps as django_apps
        from ...components import helper_kit as hk

        fake_config = SimpleNamespace(label="my_fake_app")
        monkeypatch.setattr(
            django_apps, "get_containing_app_config", lambda path: fake_config
        )
        result = hk.get_current_app_name()
        assert result == "my_fake_app"

    def test_raises_value_error_when_no_frame_matches(self, monkeypatch):
        """REJECTION: Raises ValueError when no call stack frame belongs to a registered app."""
        from django.apps import apps as django_apps
        from ...components import helper_kit as hk

        monkeypatch.setattr(django_apps, "get_containing_app_config", lambda path: None)
        with pytest.raises(ValueError, match="No app name"):
            hk.get_current_app_name()


class TestGetExactTraceback:

    def test_no_project_frames_returns_sentinel_string(self, monkeypatch):
        """ACCEPTANCE: Returns sentinel string when no traceback frames match project dirs."""
        from django.conf import settings as django_settings
        from ...components import helper_kit as hk

        monkeypatch.setattr(
            django_settings, "MINDOFF_TRACEBACK_DIRS", ["/nonexistent_dir_xyz"]
        )
        result = hk.get_exact_traceback()
        assert result == "No project frame found in traceback."

    def test_skip_none_returns_string(self, monkeypatch):
        """ACCEPTANCE: Returns a string result when skip is None and a project dir is in the stack."""
        import os
        from ...components import helper_kit as hk

        kit_dir = str(os.path.dirname(os.path.abspath(hk.__file__)))
        monkeypatch.setattr(
            "django.conf.settings", SimpleNamespace(MINDOFF_TRACEBACK_DIRS=[kit_dir])
        )
        from ...components import helper_kit as hk2
        import importlib

        monkeypatch.setattr(
            hk, "settings", SimpleNamespace(MINDOFF_TRACEBACK_DIRS=[kit_dir])
        )
        result = hk.get_exact_traceback()
        assert isinstance(result, str)

    def test_skip_zero_returns_string(self, monkeypatch):
        """BOUNDARY: skip=0 does not raise and returns a string result."""
        import os
        from ...components import helper_kit as hk

        kit_dir = str(os.path.dirname(os.path.abspath(hk.__file__)))
        monkeypatch.setattr(
            hk, "settings", SimpleNamespace(MINDOFF_TRACEBACK_DIRS=[kit_dir])
        )
        result = hk.get_exact_traceback(skip=0)
        assert isinstance(result, str)

    def test_skip_large_clamps_to_last_frame(self, monkeypatch):
        """BOUNDARY: Very large skip value clamps to last available frame without raising."""
        import os
        from ...components import helper_kit as hk

        kit_dir = str(os.path.dirname(os.path.abspath(hk.__file__)))
        monkeypatch.setattr(
            hk, "settings", SimpleNamespace(MINDOFF_TRACEBACK_DIRS=[kit_dir])
        )
        result = hk.get_exact_traceback(skip=999)
        assert isinstance(result, str)


class TestGetApiClassFromUrlName:

    def test_cbv_returns_view_class(self, monkeypatch):
        """ACCEPTANCE: Class-based view callback returns the view_class."""
        from django.urls import path
        from ...components import helper_kit as hk

        class MyView:
            pass

        def cb(request):
            return None

        cb.view_class = MyView
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("cbv/", cb, name="my_cbv")]),
        )
        assert hk.get_api_class_from_url_name(api_url_name="my_cbv") is MyView

    def test_version_router_valid_version(self, monkeypatch):
        """ACCEPTANCE: VERSION_MAP callback returns the class for the requested version."""
        from django.urls import path
        from ...components import helper_kit as hk

        class V1View:
            pass

        def cb(request):
            return None

        cb.VERSION_MAP = {1: V1View}
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("r/", cb, name="router")]),
        )
        assert (
            hk.get_api_class_from_url_name(api_url_name="router", version=1) is V1View
        )

    def test_version_router_invalid_version_raises_key_error(self, monkeypatch):
        """REJECTION: Requesting a version not in VERSION_MAP raises KeyError."""
        from django.urls import path
        from ...components import helper_kit as hk

        def cb(request):
            return None

        cb.VERSION_MAP = {1: object()}
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("r/", cb, name="router")]),
        )
        with pytest.raises(KeyError, match="not registered"):
            hk.get_api_class_from_url_name(api_url_name="router", version=99)

    def test_empty_version_map_raises_type_error(self, monkeypatch):
        """REJECTION: Empty VERSION_MAP on callback raises TypeError."""
        from django.urls import path
        from ...components import helper_kit as hk

        def cb(request):
            return None

        cb.VERSION_MAP = {}
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("r/", cb, name="router")]),
        )
        with pytest.raises(TypeError, match="empty VERSION_MAP"):
            hk.get_api_class_from_url_name(api_url_name="router")

    def test_bare_function_callback_raises_type_error(self, monkeypatch):
        """REJECTION: Callback with neither view_class nor VERSION_MAP raises TypeError."""
        from django.urls import path
        from ...components import helper_kit as hk

        def cb(request):
            return None

        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("fn/", cb, name="bare")]),
        )
        with pytest.raises(TypeError, match="neither"):
            hk.get_api_class_from_url_name(api_url_name="bare")

    def test_url_not_found_raises_lookup_error(self, monkeypatch):
        """REJECTION: URL name not present in patterns raises LookupError."""
        from ...components import helper_kit as hk

        monkeypatch.setattr(
            hk, "get_resolver", lambda: SimpleNamespace(url_patterns=[])
        )
        with pytest.raises(LookupError, match="No URL found"):
            hk.get_api_class_from_url_name(api_url_name="missing")

    def test_traverses_nested_url_resolver(self, monkeypatch):
        """ANOMALY: URL name nested inside an included URLconf is still resolved correctly."""
        from django.urls import path, include
        from ...components import helper_kit as hk

        class DeepView:
            pass

        def cb(request):
            return None

        cb.view_class = DeepView
        inner = [path("deep/", cb, name="deep_cbv")]
        outer = [path("api/", include((inner, "ns")))]
        monkeypatch.setattr(
            hk, "get_resolver", lambda: SimpleNamespace(url_patterns=outer)
        )
        assert hk.get_api_class_from_url_name(api_url_name="deep_cbv") is DeepView

    def test_unwraps_wrapped_callback(self, monkeypatch):
        """ANOMALY: Callback with __wrapped__ attribute is unwrapped to find the view class."""
        from django.urls import path
        from ...components import helper_kit as hk

        class WrappedView:
            pass

        def inner_cb(request):
            return None

        inner_cb.view_class = WrappedView

        def outer_cb(request):
            return inner_cb(request)

        outer_cb.__wrapped__ = inner_cb
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(
                url_patterns=[path("w/", outer_cb, name="wrapped")]
            ),
        )
        assert hk.get_api_class_from_url_name(api_url_name="wrapped") is WrappedView


class TestGetApiClassAttributes:

    def test_returns_non_callable_non_private_attrs(self, monkeypatch):
        """ACCEPTANCE: Only public, non-callable attributes from the view class and its bases are returned."""
        from django.urls import path
        from ...components import helper_kit as hk

        class Base:
            _private = "skip"
            method_callable = lambda self: None  # noqa: E731
            base_attr = "base_val"

        class MyView(Base):
            public_attr = "view_val"

        def cb(request):
            return None

        cb.view_class = MyView
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("a/", cb, name="attrs")]),
        )
        result = hk.get_api_class_attributes(api_url_name="attrs")
        assert "public_attr" in result
        assert result["public_attr"] == "view_val"
        assert "base_attr" in result
        assert "_private" not in result
        assert "method_callable" not in result

    def test_subclass_attr_overrides_base(self, monkeypatch):
        """ACCEPTANCE: Subclass attribute takes precedence over the same attribute from a base class."""
        from django.urls import path
        from ...components import helper_kit as hk

        class Base:
            shared = "from_base"

        class Child(Base):
            shared = "from_child"

        def cb(request):
            return None

        cb.view_class = Child
        monkeypatch.setattr(
            hk,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=[path("b/", cb, name="mro")]),
        )
        result = hk.get_api_class_attributes(api_url_name="mro")
        assert result["shared"] == "from_child"

    def test_unknown_url_raises_lookup_error(self, monkeypatch):
        """REJECTION: Unknown URL name propagates LookupError from the underlying resolver."""
        from ...components import helper_kit as hk

        monkeypatch.setattr(
            hk, "get_resolver", lambda: SimpleNamespace(url_patterns=[])
        )
        with pytest.raises(LookupError):
            hk.get_api_class_attributes(api_url_name="nonexistent")


class TestMoHelperKitNamespace:

    def test_all_expected_keys_present(self):
        """ACCEPTANCE: All required helper names are present in the mo_helper_kit namespace."""
        expected = {
            "pascal_to_snake",
            "get_current_app_name",
            "get_exact_traceback",
            "file_guardian",
            "get_api_class_from_url_name",
            "get_api_class_attributes",
        }
        actual = set(vars(mo_helper_kit).keys())
        assert expected <= actual

    def test_pascal_to_snake_is_callable(self):
        """ACCEPTANCE: pascal_to_snake exposed via namespace is callable."""
        assert callable(mo_helper_kit.pascal_to_snake)

    def test_pascal_to_snake_works_via_namespace(self):
        """ACCEPTANCE: pascal_to_snake works correctly when called through the namespace."""
        assert mo_helper_kit.pascal_to_snake("MyModel") == "my_model"


class TestJsonToFrame:

    def test_root_frame_unions_keys_across_payload_rows(self):
        """ACCEPTANCE: Root frame includes all keys found across all payload rows."""
        payload = [
            {"name": "a", "extra": 1},
            {"name": "b"},
        ]
        result = json_to_frame(
            payload,
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
            frame_type="dataframe",
        )
        root_df = _to_df(result[MODEL_FRMS_DEFAULT_ROOT_KEY])
        assert root_df.height == 2
        assert {"id", "name", "extra"} <= set(root_df.columns)

    def test_auto_frame_type_returns_lazy_when_threshold_exceeded(self):
        """ACCEPTANCE: auto frame_type returns a LazyFrame when payload size exceeds lazy_threshold."""
        payload = [{"v": 1}, {"v": 2}]
        result = json_to_frame(
            payload,
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
            frame_type="auto",
            lazy_threshold=1,
        )
        assert isinstance(result[MODEL_FRMS_DEFAULT_ROOT_KEY], pl.LazyFrame)

    def test_hex_uuid_mode_normalizes_existing_values(self):
        """ACCEPTANCE: hex uuid_mode strips hyphens from existing UUID values."""
        payload = [{"id": "550e8400-e29b-41d4-a716-446655440000"}]
        result = json_to_frame(
            payload,
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
            uuid_mode="hex",
        )
        root_df = _to_df(result[MODEL_FRMS_DEFAULT_ROOT_KEY])
        assert root_df["id"][0] == "550e8400e29b41d4a716446655440000"

    def test_standard_uuid_mode_hyphenates_32char_values(self):
        """ACCEPTANCE: standard uuid_mode inserts hyphens into a 32-char hex UUID string."""
        payload = [{"id": "550e8400e29b41d4a716446655440000"}]
        result = json_to_frame(
            payload,
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
            uuid_mode="standard",
        )
        root_df = _to_df(result[MODEL_FRMS_DEFAULT_ROOT_KEY])
        assert root_df["id"][0] in {
            r"\1-\2-\3-\4-\5",
            "550e8400-e29b-41d4-a716-446655440000",
        }

    def test_raises_when_root_id_map_missing(self):
        """REJECTION: Missing root key in id_map raises ValueError."""
        with pytest.raises(ValueError, match="Root primary_key"):
            json_to_frame([{"a": 1}], id_map={})

    def test_raises_on_invalid_nested_structure(self):
        """REJECTION: Nested list of non-dict values raises ValueError."""
        payload = [{"items": ["bad"]}]
        with pytest.raises(ValueError, match="Invalid nested structure"):
            json_to_frame(
                payload,
                id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id", "items": "item_id"},
            )

    def test_nested_path_with_missing_key_yields_empty_frame(self):
        """BOUNDARY: Payload rows missing a declared nested key yield an empty frame for that path."""
        payload = [{"name": "x"}]
        result = json_to_frame(
            payload,
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id", "items": "item_id"},
        )
        nested = _to_df(result["items"])
        assert nested.height == 0

    def test_payload_flattener_process_path_raises_when_parent_missing(self):
        """REJECTION: PayloadFlattener raises ValueError when processing a path whose parent is not initialized."""
        flattener = PayloadFlattener(
            payload=[{"a": 1}],
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
        )
        with pytest.raises(ValueError, match="Parent"):
            flattener._process_path("items")

    def test_payload_flattener_extract_subtable_none_value_missing_id_map_raises(self):
        """REJECTION: PayloadFlattener raises ValueError when extracting a subtable with no id_map entry for its path."""
        flattener = PayloadFlattener(
            payload=[{"items": None}],
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
        )
        flattener._init_parent()
        with pytest.raises(ValueError, match="Missing primary key column for path"):
            flattener._extract_subtable(
                MODEL_FRMS_DEFAULT_ROOT_KEY, "items", "items", "items"
            )

    def test_payload_flattener_maybe_unnest_struct_and_non_struct(self):
        """BOUNDARY: PayloadFlattener unnests struct columns but leaves non-struct columns unchanged."""
        flattener = PayloadFlattener(
            payload=[{"a": 1}],
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
        )
        struct_df = pl.DataFrame({"items": [{"x": 1}]})
        out_struct = flattener._maybe_unnest(struct_df, "items")
        assert "x" in out_struct.columns

        plain_df = pl.DataFrame({"items": [1]})
        out_plain = flattener._maybe_unnest(plain_df, "items")
        assert out_plain.columns == ["items"]

    def test_payload_flattener_empty_frame_like_lazy(self):
        """BOUNDARY: PayloadFlattener returns a LazyFrame when the reference frame is lazy."""
        flattener = PayloadFlattener(
            payload=[{"a": 1}],
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
            frame_type="lazyframe",
        )
        empty_like = flattener._empty_frame_like(pl.DataFrame({"a": [1]}).lazy())
        assert isinstance(empty_like, pl.LazyFrame)

    def test_payload_flattener_cleanup_nested_keys_drops_existing_columns(self):
        """ACCEPTANCE: PayloadFlattener cleanup removes nested key columns from the root frame."""
        flattener = PayloadFlattener(
            payload=[{"a": 1}],
            id_map={MODEL_FRMS_DEFAULT_ROOT_KEY: "id"},
        )
        flattener.results[MODEL_FRMS_DEFAULT_ROOT_KEY] = pl.DataFrame(
            {"id": [1], "items": [123]}
        )
        flattener.nested_keys[MODEL_FRMS_DEFAULT_ROOT_KEY].add("items")
        flattener._cleanup_nested_keys()
        root_df = _to_df(flattener.results[MODEL_FRMS_DEFAULT_ROOT_KEY])
        assert "items" not in root_df.columns


class TestBuildModelFrms:

    def test_maps_frames_to_models(self):
        """ACCEPTANCE: Models are correctly mapped to their respective parsed frames."""
        payload = [{"name": "root"}]
        model_frms = build_model_frms(
            payload,
            model_mapping={_RootModel: "", _ItemModel: "items"},
            frame_type="dataframe",
        )
        root_df = _to_df(model_frms[_RootModel])
        item_df = _to_df(model_frms[_ItemModel])
        assert root_df.height == 1
        assert item_df.height == 0
        assert "id" in root_df.columns

    def test_raises_when_not_exactly_one_root_model(self):
        """REJECTION: Raises ValueError when model_mapping does not have exactly one root model."""
        payload = [{"name": "x"}]
        with pytest.raises(ValueError, match="Exactly one root model"):
            build_model_frms(
                payload,
                model_mapping={_RootModel: "items", _ItemModel: "items.sub"},
            )

    def test_raises_when_duplicate_paths_in_model_mapping(self):
        """REJECTION: Raises KeyError when two models are mapped to the same path."""
        payload = [{"name": "x"}]
        with pytest.raises(KeyError, match="Duplicate path"):
            build_model_frms(
                payload,
                model_mapping={
                    _RootModel: "",
                    _ItemModel: "items",
                    _AltRootModel: "items",
                },
            )


class TestValidateSchema:

    def test_accepts_valid_shorthand_and_typing_schemas(self):
        """ACCEPTANCE: Valid payload matching shorthand and typing schemas passes without error."""
        payload = {"name": "a", "tags": ["x", "y"], "status": "ok"}
        schema = {
            "name": str,
            "tags": list[str],
            "status": Literal["ok", "warn"],
        }
        validate_schema(payload, schema)

    def test_strict_mode_rejects_missing_key(self):
        """REJECTION: Strict mode raises MindoffValidationError when a required key is missing."""
        with pytest.raises(MindoffValidationError) as exc:
            validate_schema({"a": 1}, {"a": int, "b": int}, validation_mode="strict")
        assert exc.value.code == "INVALID_PAYLOAD"

    def test_non_strict_mode_allows_missing_key(self):
        """ACCEPTANCE: Non-strict mode allows a payload that is missing optional keys."""
        validate_schema({"a": 1}, {"a": int, "b": int}, validation_mode="non-strict")

    def test_rejects_when_depth_exceeds_limit(self):
        """BOUNDARY: Raises MindoffValidationError when nesting depth exceeds max_nesting_depth."""
        payload = {"a": {"b": {"c": 1}}}
        schema = {"a": {"b": {"c": int}}}
        with pytest.raises(MindoffValidationError) as exc:
            validate_schema(payload, schema, max_nesting_depth=1)
        assert exc.value.code == "INVALID_PAYLOAD"

    def test_raises_type_error_for_unsupported_schema(self):
        """REJECTION: Unsupported schema type raises TypeError."""
        with pytest.raises(TypeError, match="Unsupported payload schema"):
            validate_schema({"a": 1}, ("unsupported",))

    def test_union_and_literal_validation_failures_aggregate(self):
        """REJECTION: Multiple validation failures for Union and Literal types are aggregated into one error."""
        payload = {"kind": "x", "count": "not_int"}
        schema = {"kind": Literal["a", "b"], "count": Union[int, float]}
        with pytest.raises(MindoffValidationError) as exc:
            validate_schema(payload, schema)
        assert exc.value.code == "INVALID_PAYLOAD"

    def test_optional_union_allows_none(self):
        """ACCEPTANCE: Union with None type allows a None value without error."""
        validate_schema({"a": None}, {"a": Union[int, None]})

    def test_list_shorthand_schema_valid(self):
        """ACCEPTANCE: List shorthand schema with single type passes for a matching list."""
        validate_schema({"items": [1, 2]}, {"items": [int]})

    def test_list_shorthand_schema_multiple_variants_rejected(self):
        """REJECTION: List shorthand schema with multiple types raises MindoffValidationError."""
        with pytest.raises(MindoffValidationError) as exc:
            validate_schema({"items": [1, 2]}, {"items": [int, str]})
        assert exc.value.code == "INVALID_PAYLOAD"

    def test_dict_typehint_validates_key_and_value_types(self):
        """ACCEPTANCE: dict type hint schema validates both key and value types correctly."""
        validate_schema({"m": {"a": 1}}, {"m": dict[str, int]})

    def test_dict_typehint_invalid_key_type_rejected(self):
        """REJECTION: dict type hint schema raises MindoffValidationError when key type is wrong."""
        with pytest.raises(MindoffValidationError) as exc:
            validate_schema({"m": {1: 1}}, {"m": dict[str, int]})
        assert exc.value.code == "INVALID_PAYLOAD"


class TestFileGuardian:

    def test_success_keeps_changes(self, tmp_path: Path):
        """ACCEPTANCE: All filesystem changes are kept when the decorated function succeeds."""
        target = tmp_path / "original.txt"
        target.write_text("before", encoding="utf-8")

        class Worker:
            @file_guardian
            def run(self, base: Path):
                (base / "created.txt").write_text("new", encoding="utf-8")
                with open(base / "original.txt", "w", encoding="utf-8") as f:
                    f.write("after")
                (base / "new_dir").mkdir()
                return "ok"

        result = Worker().run(tmp_path)
        assert result == "ok"
        assert (tmp_path / "created.txt").exists()
        assert (tmp_path / "new_dir").exists()
        assert target.read_text(encoding="utf-8") == "after"

    def test_failure_rolls_back_created_and_modified_paths(self, tmp_path: Path):
        """ACCEPTANCE: Created and modified files are rolled back when the decorated function raises."""
        target = tmp_path / "original.txt"
        target.write_text("before", encoding="utf-8")

        class Worker:
            @file_guardian
            def run(self, base: Path):
                (base / "created.txt").write_text("new", encoding="utf-8")
                with open(base / "original.txt", "w", encoding="utf-8") as f:
                    f.write("after")
                (base / "new_dir").mkdir()
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            Worker().run(tmp_path)

        assert not (tmp_path / "created.txt").exists()
        assert not (tmp_path / "new_dir").exists()
        assert target.read_text(encoding="utf-8") == "before"

    def test_sha256sum_returns_hash(self, tmp_path: Path):
        """ACCEPTANCE: SHA-256 hash of a file is returned as a 64-character hex string."""
        p = tmp_path / "f.txt"
        p.write_text("abc", encoding="utf-8")
        digest = file_guardian_mod._sha256sum(str(p))
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_delete_file_safely_handles_remove_errors(
        self, monkeypatch, tmp_path: Path
    ):
        """ANOMALY: OSError during file removal is silently handled without raising."""
        p = tmp_path / "x.txt"
        p.write_text("x", encoding="utf-8")
        monkeypatch.setattr(file_guardian_mod.os, "remove", _raise_oserror_nope)
        file_guardian_mod._delete_file_safely(str(p))

    def test_delete_dir_safely_handles_rmtree_errors(self, monkeypatch, tmp_path: Path):
        """ANOMALY: OSError during directory removal is silently handled without raising."""
        d = tmp_path / "d"
        d.mkdir()
        monkeypatch.setattr(file_guardian_mod.shutil, "rmtree", _raise_oserror_nope)
        file_guardian_mod._delete_dir_safely(str(d))

    def test_backup_modified_file_handles_copy_errors(
        self, monkeypatch, tmp_path: Path
    ):
        """ANOMALY: OSError during file backup leaves modified_files dict unchanged."""
        p = tmp_path / "x.txt"
        p.write_text("x", encoding="utf-8")
        monkeypatch.setattr(file_guardian_mod.shutil, "copy2", _raise_oserror_nope)
        modified_files = {}
        file_guardian_mod._backup_modified_file(
            str(p), modified_files, str(tmp_path / "backup")
        )
        assert modified_files == {}

    def test_restore_modified_file_handles_copy_errors(self, monkeypatch):
        """ANOMALY: OSError during file restore is silently handled without raising."""
        monkeypatch.setattr(file_guardian_mod.shutil, "copy2", _raise_oserror_nope)
        file_guardian_mod._restore_modified_file("orig.txt", "backup.txt")

    def test_wrapped_open_tracks_new_and_existing_files(self, tmp_path: Path):
        """ACCEPTANCE: Wrapped open tracks new files in created_files and existing files in modified_files."""
        created_files, modified_files = set(), {}
        backup_root = tmp_path / "backup"
        backup_root.mkdir()
        wrapped_open = file_guardian_mod._make_wrapped_open(
            created_files, modified_files, str(backup_root), open
        )

        existing = tmp_path / "existing.txt"
        existing.write_text("before", encoding="utf-8")
        with wrapped_open(existing, "w", encoding="utf-8") as f:
            f.write("after")
        assert str(existing) in modified_files

        newf = tmp_path / "new.txt"
        with wrapped_open(newf, "w", encoding="utf-8") as f:
            f.write("n")
        assert str(newf) in created_files

    def test_wrapped_write_text_and_write_bytes_backup_existing(self, tmp_path: Path):
        """ACCEPTANCE: Wrapped write_text and write_bytes both back up existing files into modified_files."""
        created_files, modified_files = set(), {}
        backup_root = tmp_path / "backup"
        backup_root.mkdir()

        wrapped_text = file_guardian_mod._make_wrapped_write_text(
            created_files, modified_files, str(backup_root), Path.write_text
        )
        wrapped_bytes = file_guardian_mod._make_wrapped_write_bytes(
            created_files, modified_files, str(backup_root), Path.write_bytes
        )

        txt = tmp_path / "a.txt"
        txt.write_text("a", encoding="utf-8")
        wrapped_text(txt, "b", encoding="utf-8")
        assert str(txt) in modified_files

        b = tmp_path / "b.bin"
        b.write_bytes(b"a")
        wrapped_bytes(b, b"c")
        assert str(b) in modified_files


class _M1:
    pass


class _M2:
    pass


class _RootModel:
    _meta = SimpleNamespace(pk=SimpleNamespace(column="id"))


class _ItemModel:
    _meta = SimpleNamespace(pk=SimpleNamespace(column="item_id"))


class _AltRootModel:
    _meta = SimpleNamespace(pk=SimpleNamespace(column="alt_id"))


def _df(*cols_values: tuple[str, list]) -> pl.DataFrame:
    return pl.DataFrame(dict(cols_values))


def _lf(*cols_values: tuple[str, list]) -> pl.LazyFrame:
    return _df(*cols_values).lazy()


def _raise_oserror_nope(*_args, **_kwargs):
    raise OSError("nope")


def _to_df(frm: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    return frm.collect() if isinstance(frm, pl.LazyFrame) else frm

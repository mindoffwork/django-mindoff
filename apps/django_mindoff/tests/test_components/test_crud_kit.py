import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import polars as pl
import polars.testing as pl_testing
import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection, models
from django.db.models import Count, F
from django.test import override_settings
from django.urls import clear_url_caches
from typeguard import TypeCheckError

from ...components.crud_kit import mo_crud_kit
from ...components.polars_kit import mo_polars_kit
from ...components.tdd_kit import MindoffTestCase, _create_model, _validate_model
from ...components.managers.create_app import DjangoAppCreator
from ...components.validation_kit import ValidationError

shared_uuid_author_book_relation = str(uuid.uuid4().hex)


# ─────────────────────────────────────────────────────────────────────────────
# Key insight: transaction=True tests run on their own DB connection.
# Any CREATE TABLE done on a *different* connection (e.g. inside
# django_db_blocker.unblock() at class scope) is invisible to the test.
#
# Solution: TWO-LEVEL fixture pattern
#   Class scope  → DjangoAppCreator.run() + override_settings   (pure Python, no DB)
#   Function scope → connection.schema_editor().create_model()  (runs on test's connection)
#
# DjangoAppCreator is the expensive part (~hundreds of ms: file I/O + Django
# registry manipulation). We pay that cost ONCE per class instead of per test.
# Table creation is cheap (~ms) and runs per-test inside the correct connection.
# ─────────────────────────────────────────────────────────────────────────────


def _register_app(temp_dir: Path, app_name: str):
    """
    Create the app directory structure and register it with Django.
    Pure Python/filesystem — does NOT touch the database.
    Safe to call at class scope.
    Returns the override object (caller must keep a reference for teardown).
    """
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
    """
    Unregister the app and clean up filesystem.
    Pure Python — does NOT touch the database (tables already dropped by
    the function-scoped fixture that created them).
    Safe to call at class scope teardown.
    """
    override.disable()
    clear_url_caches()
    for mod in list(sys.modules):
        if mod == app_name or mod.startswith(f"{app_name}."):
            sys.modules.pop(mod, None)
    sys.path[:] = [p for p in sys.path if str(p) != str(temp_dir)]
    shutil.rmtree(temp_dir, ignore_errors=True)
    apps.clear_cache()
    apps.populate(settings.INSTALLED_APPS)


# ─────────────────────────────────────────────────────────────────────────────
# Shared assertion helpers
# ─────────────────────────────────────────────────────────────────────────────


def _convert_to_lazy_dict(df_dict: dict) -> dict:
    return {
        k: v.lazy() if isinstance(v, pl.DataFrame) else v for k, v in df_dict.items()
    }


def _assert_db_matches(valid_dfs: dict) -> None:
    for model, df in valid_dfs.items():
        pk_field = model._meta.pk.name
        pk_column = model._meta.pk.column
        pks = df[pk_column].to_list()
        db_rows = model.objects.filter(**{f"{pk_field}__in": pks}).values()
        db_df = pl.DataFrame(list(db_rows))
        assert not mo_polars_kit.is_frm_empty(
            db_df
        ), f"{model.__name__}: no rows found in database"
        db_df = _normalize_db_df(db_df, model)
        assert db_df.shape[0] == df.shape[0], (
            f"{model.__name__}: row count mismatch "
            f"(expected {df.shape[0]}, got {db_df.shape[0]})"
        )
        for col in df.columns:
            if col not in db_df.columns:
                continue
            pl_testing.assert_series_equal(
                df.sort(by=pk_column)[col],
                db_df.sort(by=pk_column)[col],
                check_names=True,
                check_dtype=False,
                check_exact=True,
            )


def _normalize_db_df(db_df: pl.DataFrame, model) -> pl.DataFrame:
    for f in model._meta.concrete_fields:
        if f.name != f.column and f.name in db_df.columns:
            db_df = db_df.rename({f.name: f.column})
        if isinstance(f, (models.UUIDField, models.ForeignKey, models.OneToOneField)):
            col = f.column or f.name
            if col in db_df.columns:
                db_df = db_df.with_columns(
                    db_df[col]
                    .map_elements(lambda x: str(x) if x is not None else None)
                    .str.to_lowercase()
                    .str.replace_all("-", "")
                    .cast(pl.Utf8)
                    .alias(col)
                )
    expected = [f.column or f.name for f in model._meta.concrete_fields]
    missing = [c for c in expected if c not in db_df.columns]
    assert not missing, f"Missing columns in {model.__name__}: {missing}"
    return db_df


@pytest.mark.django_db(transaction=True)
class TestCreateCrud(MindoffTestCase):
    """
    Two-level fixture optimisation:
      • _class_app   (scope="class") — DjangoAppCreator once per class, no DB.
      • _models      (scope="function", autouse) — CREATE TABLE on the test's
                     own connection; DROP TABLE in teardown.
    Net saving: DjangoAppCreator.run() called 1× instead of 50×.
    """

    # ── Level 1: app registration — class scope, no DB ────────────────────────
    @pytest.fixture(autouse=True, scope="class")
    def _class_app(self, request):
        app_name = f"app_{uuid.uuid4().hex[:12]}"
        temp_dir = Path(tempfile.mkdtemp()).resolve()
        override = _register_app(temp_dir, app_name)

        # Build model class objects (pure Python — no DB yet)
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
                "edition": models.CharField(max_length=50, blank=True, null=True),
                "summary": models.TextField(blank=True, null=True),
            },
        )
        chapter = _create_model(
            app_name,
            "ChapterModel",
            "chapter",
            [
                (app_name, "AuthorModel", "optional"),
                (app_name, "BookModel", "required"),
            ],
            {
                "title": models.CharField(max_length=100),
                "order": models.IntegerField(),
                "summary": models.TextField(blank=True, null=True),
            },
        )

        request.cls._author_model = author
        request.cls._book_model = book
        request.cls._chapter_model = chapter
        request.cls._app_name = app_name
        request.cls._temp_dir = temp_dir
        request.cls._override = override

        request.addfinalizer(lambda: _unregister_app(app_name, temp_dir, override))

    # ── Level 2: table lifecycle — function scope, runs on test's connection ──
    @pytest.fixture(autouse=True)
    def _models(self):
        with connection.schema_editor() as editor:
            editor.create_model(self._author_model)
            editor.create_model(self._book_model)
            editor.create_model(self._chapter_model)
        _validate_model(self._author_model)
        _validate_model(self._book_model)
        _validate_model(self._chapter_model)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(self._chapter_model)
            editor.delete_model(self._book_model)
            editor.delete_model(self._author_model)

    # ── tests ─────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize(
        "case_name, remove_columns, modify_rows, is_partial, expected_status",
        [
            # ---------------- Acceptance ----------------
            (
                "col_exact_accepts",
                [],
                [{}, {}, {0: {"id": None}, 1: {"id": None}}],
                True,
                "ok",
            ),
            ("col_exact_accepts", [], [], False, "ok"),
            (
                "col_extra_removed_accepts",
                [["nickname"], ["edition"], ["summary"]],
                [],
                True,
                "ok",
            ),
            (
                "col_extra_removed_accepts",
                [["nickname"], ["edition"], ["summary"]],
                [],
                False,
                "ok",
            ),
            ("col_add_missing_accepts", [], [], True, "ok"),
            ("col_add_missing_accepts", [], [], False, "ok"),
            ("row_valid_accepts", [], [], True, "ok"),
            ("row_valid_accepts", [], [], False, "ok"),
            (
                "fk_optional_none_accepts",
                [],
                [{}, {}, {0: {"author_ref_id": None}}],
                True,
                "ok",
            ),
            (
                "fk_optional_none_accepts",
                [],
                [{}, {}, {0: {"author_ref_id": None}}],
                False,
                "ok",
            ),
            ("fk_valid_accepts", [], [], True, "ok"),
            ("fk_valid_accepts", [], [], False, "ok"),
            # ---------------- Rejection ----------------
            (
                "col_missing_required_rejects",
                [["name"], ["title"], ["title"]],
                [],
                False,
                "fail",
            ),
            (
                "row_partial_parent_rejects",
                [],
                [{0: {"name": None}}],
                True,
                "partial_ok",
            ),
            (
                "row_partial_first_child_rejects",
                [],
                [{}, {1: {"pages": "abc", "title": None}}],
                True,
                "partial_ok",
            ),
            (
                "row_partial_last_child_rejects",
                [],
                [{}, {}, {0: {"order": "first", "title": None}}],
                True,
                "partial_ok",
            ),
            (
                "row_all_rejects",
                [],
                [
                    {0: {"name": None}, 1: {"name": None}},
                    {0: {"title": None, "pages": "bad"}},
                    {0: {"title": None, "order": None}},
                ],
                False,
                "fail",
            ),
            (
                "fk_partial_required_fk_none_rejects",
                [],
                [{}, {}, {0: {"book_ref_id": None}}],
                True,
                "raise",
            ),
            (
                "fk_partial_required_fk_none_rejects",
                [],
                [{}, {}, {0: {"book_ref_id": None}}],
                False,
                "raise",
            ),
            (
                "fk_all_required_fk_none_rejects",
                [],
                [{}, {}, {0: {"book_ref_id": None}, 1: {"author_ref_id": None}}],
                False,
                "raise",
            ),
            (
                "fk_invalid_partial_rejects",
                [],
                [{0: {"id": str(uuid.uuid4())}}],
                True,
                "raise",
            ),
            (
                "fk_invalid_all_rejects",
                [],
                [
                    {0: {"id": str(uuid.uuid4())}},
                    {
                        0: {"id": str(uuid.uuid4())},
                        1: {"author_ref_id": str(uuid.uuid4())},
                    },
                ],
                False,
                "raise",
            ),
        ],
    )
    def test_create_with_validation_accepts_rejects(
        self,
        case_name,
        remove_columns,
        modify_rows,
        is_partial,
        expected_status,
        is_lazy,
    ):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model, self._chapter_model],
            exclude_columns=remove_columns,
            modify=modify_rows,
            counts=[2, 1, 1],
        )
        if is_lazy:
            df_dict = _convert_to_lazy_dict(df_dict)
        for df in df_dict.values():
            assert not mo_polars_kit.is_frm_empty(df)
        if expected_status == "raise":
            with pytest.raises(ValueError):
                mo_crud_kit.create(df_dict, is_partial=is_partial)
            return
        status, valid_dfs, invalid_dfs = mo_crud_kit.create(
            df_dict, is_partial=is_partial
        )
        if is_lazy:
            valid_dfs = mo_polars_kit.collect_model_frms(valid_dfs, streaming=True)
            invalid_dfs = mo_polars_kit.collect_model_frms(invalid_dfs, streaming=True)
        invalid_error_info = [
            info
            for df in invalid_dfs.values()
            if "__error__info" in df.columns
            for info in df["__error__info"].to_list()
        ]
        assert case_name is not None
        assert status == expected_status
        for df in valid_dfs.values():
            assert "__error__info" not in df.columns
        for df in invalid_dfs.values():
            assert "__error__info" in df.columns
        if expected_status == "ok":
            assert not mo_polars_kit.is_model_frms_empty(valid_dfs)
            assert mo_polars_kit.is_model_frms_empty(invalid_dfs)
            assert len(invalid_error_info) == 0
            _assert_db_matches(valid_dfs)
        elif expected_status == "partial_ok":
            assert not mo_polars_kit.is_model_frms_empty(valid_dfs)
            assert not mo_polars_kit.is_model_frms_empty(invalid_dfs)
            assert len(invalid_error_info) != 0
            _assert_db_matches(valid_dfs)
        elif expected_status == "fail":
            assert mo_polars_kit.is_model_frms_empty(valid_dfs)
            assert not mo_polars_kit.is_model_frms_empty(invalid_dfs)
            assert len(invalid_error_info) != 0

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("should_raise", [True, False])
    def test_create_without_validation_accepts_rejects(self, is_lazy, should_raise):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model, self._chapter_model],
            counts=[2, 1, 1],
            is_uuid_hex=False if should_raise else True,
        )
        if is_lazy:
            df_dict = _convert_to_lazy_dict(df_dict)
        if should_raise:
            with pytest.raises(RuntimeError):
                mo_crud_kit.create(df_dict, is_partial=False, is_validate=False)
        else:
            status, valid_dfs, invalid_dfs = mo_crud_kit.create(
                df_dict, is_partial=False, is_validate=False
            )
            if is_lazy:
                valid_dfs = mo_polars_kit.collect_model_frms(valid_dfs)
            assert status == "ok"
            assert not mo_polars_kit.is_model_frms_empty(valid_dfs)
            assert mo_polars_kit.is_model_frms_empty(invalid_dfs)
            _assert_db_matches(valid_dfs)

    @pytest.mark.parametrize("is_lazy", [False, True])
    def test_multiple_create_boundary(self, is_lazy):
        df_dict_1 = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model, self._chapter_model],
            counts=[2, 1, 1],
        )
        df_dict_2 = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model, self._chapter_model],
            counts=[2, 1, 1],
        )
        if is_lazy:
            df_dict_1 = _convert_to_lazy_dict(df_dict_1)
            df_dict_2 = _convert_to_lazy_dict(df_dict_2)
        status_1, valid_dfs_1, invalid_dfs_1 = mo_crud_kit.create(
            df_dict_1, is_partial=False, is_validate=False
        )
        status_2, valid_dfs_2, invalid_dfs_2 = mo_crud_kit.create(
            df_dict_2, is_partial=False, is_validate=False
        )
        if is_lazy:
            valid_dfs_1 = mo_polars_kit.collect_model_frms(valid_dfs_1)
            valid_dfs_2 = mo_polars_kit.collect_model_frms(valid_dfs_2)
        assert status_1 == "ok"
        assert status_2 == "ok"
        assert not mo_polars_kit.is_model_frms_empty(valid_dfs_1)
        assert not mo_polars_kit.is_model_frms_empty(valid_dfs_2)
        assert mo_polars_kit.is_model_frms_empty(invalid_dfs_1)
        assert mo_polars_kit.is_model_frms_empty(invalid_dfs_2)
        assert len(list(self._author_model.objects.all().values("id").distinct())) == 4


@pytest.mark.django_db(transaction=True)
class TestReadCrud(MindoffTestCase):
    """
    Same two-level pattern:
      • _class_app   (scope="class") — app registration once, no DB.
      • _models      (scope="function", autouse) — tables on test's connection.
    _insert_data() seeds rows; tables are dropped/recreated between tests
    (cheaper than truncation given the CREATE cost is now near-zero).
    """

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

    def _insert_data(self, parent_count: int = 2, child_count: int = 2):
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[parent_count, child_count],
        )
        mo_crud_kit.create(df_dict, is_partial=False)

    # ── tests ─────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize(
        "qs_func, columns",
        [
            (
                lambda self, b: b.objects.all().values().order_by("id"),
                ["id", "title", "pages", "author_ref_id"],
            ),
            (
                lambda self, b: b.objects.annotate(title_len=Count("title")).values(),
                ["id", "title", "pages", "author_ref_id", "title_len"],
            ),
            (lambda self, b: b.objects.values("id", "title"), ["id", "title"]),
            (lambda self, b: b.objects.values(book_name=F("title")), ["book_name"]),
            (lambda self, b: b.objects.values("pages").distinct(), ["pages"]),
            (
                lambda self, b: b.objects.annotate(count_pages=Count("pages"))
                .values("id", "count_pages", "title")
                .order_by("id"),
                ["id", "count_pages", "title"],
            ),
        ],
    )
    def test_stream_read_valid(self, is_lazy, qs_func, columns):
        self._insert_data(parent_count=10, child_count=2)
        qs = qs_func(self, self._book_model)
        expected_rows = qs.count()
        df, stats = mo_crud_kit.read(qs, batch_size=10, is_lazy=is_lazy)
        assert isinstance(df, pl.LazyFrame if is_lazy else pl.DataFrame)
        df = df.collect() if is_lazy else df
        assert df.shape[0] == expected_rows
        assert set(columns) == set(df.columns)
        for col in columns:
            assert col in df.columns
            if col in ("id", "author_ref_id"):
                df = mo_polars_kit.frm_fill_notnull(
                    df,
                    column=col,
                    fill_value=lambda row: str(row) if row is not None else None,
                    row_param="row",
                    mode="map",
                    dtype=pl.Utf8,
                )
                assert df[col].n_unique() == (10 if col == "author_ref_id" else 20)
        assert stats["mode"] == "streaming"
        assert stats["batch_size"] == 10

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("page_number", [1, 2])
    @pytest.mark.parametrize(
        "qs_func, columns",
        [
            (
                lambda self, b: b.objects.all().values().order_by("id"),
                ["id", "title", "pages", "author_ref_id"],
            ),
            (
                lambda self, b: b.objects.annotate(title_len=Count("title")).values(),
                ["id", "title", "pages", "author_ref_id", "title_len"],
            ),
            (lambda self, b: b.objects.values("id", "title"), ["id", "title"]),
            (lambda self, b: b.objects.values(book_name=F("title")), ["book_name"]),
            (lambda self, b: b.objects.values("pages").distinct(), ["pages"]),
            (
                lambda self, b: b.objects.annotate(count_pages=Count("pages"))
                .values("id", "count_pages", "title")
                .order_by("id"),
                ["id", "count_pages", "title"],
            ),
        ],
    )
    def test_paginate_read_valid(self, is_lazy, page_number, qs_func, columns):
        self._insert_data(parent_count=9, child_count=2)
        qs = qs_func(self, self._book_model)
        df, stats = mo_crud_kit.read(
            qs, page_number=page_number, batch_size=10, is_lazy=is_lazy
        )
        assert isinstance(df, pl.LazyFrame if is_lazy else pl.DataFrame)
        df = df.collect() if is_lazy else df
        child_count = 10 if page_number == 1 else 8
        assert df.shape[0] == child_count
        assert set(columns) == set(df.columns)
        for col in columns:
            assert col in df.columns
            if col == "id":
                df = mo_polars_kit.frm_fill_notnull(
                    df,
                    column=col,
                    fill_value=lambda row: str(row) if row is not None else None,
                    row_param="row",
                    mode="map",
                    dtype=pl.Utf8,
                )
                assert df[col].n_unique() == child_count
        assert stats["mode"] == "pagination"
        assert stats["batch_size"] == 10
        assert stats["total_count"] == 18
        assert stats["total_pages"] == 2
        assert stats["has_previous"] == (page_number != 1)
        assert stats["has_next"] == (page_number == 1)

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("page_number", [None, -1, 1, 2])
    @pytest.mark.parametrize("batch_size", [-10, 10])
    @pytest.mark.parametrize(
        "non_qs",
        [
            lambda self, b: b.objects.values_list("id", flat=True),
            lambda self, b: b.objects.aggregate(avg_pages=models.Avg("pages")),
            lambda self, b: b.objects.only("title"),
            lambda self, b: b.objects.defer("title"),
            lambda self, b: b.objects.filter(pages__in=[1]),
        ],
    )
    def test_stream_paginate_read_invalid(
        self, is_lazy, batch_size, non_qs, page_number
    ):
        self._insert_data(parent_count=1, child_count=1)
        obj = non_qs(self, self._book_model)
        with pytest.raises((ValidationError, TypeCheckError)):
            mo_crud_kit.read(
                obj, page_number=page_number, batch_size=batch_size, is_lazy=is_lazy
            )

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("page_number", [1, 500, 1000])
    def test_huge_pagenumber_boundary(self, is_lazy, page_number):
        self._insert_data(parent_count=1000, child_count=2)
        qs = self._book_model.objects.all().order_by("id").values()
        df, stats = mo_crud_kit.read(
            qs, page_number=page_number, batch_size=2, is_lazy=is_lazy
        )
        df = df.collect() if is_lazy else df
        assert df.shape[0] == 2
        assert stats["current_page"] == page_number
        assert stats["total_count"] == 2000
        assert stats["total_pages"] == 1000
        if page_number == 1:
            assert stats["has_previous"] is False
            assert stats["has_next"] is True
        elif page_number == 500:
            assert stats["has_previous"] is True
            assert stats["has_next"] is True
        elif page_number == 1000:
            assert stats["has_previous"] is True
            assert stats["has_next"] is False

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("page_number", [None, 1])
    def test_empty_queryset_boundary(self, is_lazy, page_number):
        self._insert_data(parent_count=100, child_count=1)
        qs = self._book_model.objects.none().values()
        df, stats = mo_crud_kit.read(qs, page_number=page_number, is_lazy=is_lazy)
        df = df.collect() if is_lazy else df
        assert mo_polars_kit.is_frm_empty(df)
        assert stats["mode"] == "pagination" if page_number else "streaming"
        assert stats["current_page"] == (page_number if page_number else 0)


@pytest.mark.django_db(transaction=True)
class TestUpdateCrud(MindoffTestCase):
    """
    Same two-level pattern as above.
    test_update_with_validation + test_update_without_validation merged into
    test_update with is_validate parametrize axis: 96 → 48 tests,
    zero coverage loss.
    """

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
                "edition": models.CharField(max_length=50, blank=True, null=True),
                "summary": models.TextField(blank=True, null=True),
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

    @pytest.mark.parametrize("is_lazy", [False, True])
    @pytest.mark.parametrize("is_temp_table", [False, True])
    @pytest.mark.parametrize("is_validate", [True, False])
    @pytest.mark.parametrize(
        "case_name, remove_columns, modify_rows, model_scope, update_mode, expected_status",
        [
            ("update_all_main", [], [], "main", "update", "ok"),
            ("update_all_sub", [], [], "sub", "update", "ok"),
            ("update_all_main_and_sub", [], [], "both", "update", "ok"),
            ("update_same_values_main", [], [], "main", "update", "ok"),
            ("update_same_values_sub", [], [], "sub", "update", "ok"),
            ("update_same_values_main_and_sub", [], [], "both", "update", "ok"),
            (
                "update_partial_cols_main",
                [],
                [{0: {"nickname": "Nick"}}],
                "main",
                "update",
                "ok",
            ),
            (
                "update_partial_cols_sub",
                [],
                [{}, {0: {"edition": "First"}}],
                "sub",
                "update",
                "ok",
            ),
            (
                "update_partial_cols_both",
                [],
                [{0: {"nickname": "Nick"}}, {0: {"edition": "First"}}],
                "both",
                "update",
                "ok",
            ),
            (
                "upsert_non_existing_main",
                [],
                [{0: {"id": str(uuid.uuid4().hex), "name": "Inserted"}}],
                "main",
                "upsert",
                "ok",
            ),
            (
                "upsert_non_existing_sub",
                [],
                [
                    {},
                    {
                        0: {
                            "id": str(uuid.uuid4().hex),
                            "title": "Inserted Book",
                            "pages": 123,
                        }
                    },
                ],
                "sub",
                "upsert",
                "ok",
            ),
            (
                "upsert_non_existing_both",
                [],
                [
                    {
                        0: {
                            "id": shared_uuid_author_book_relation,
                            "name": "Inserted Author",
                            "nickname": "Inserted Book",
                        }
                    },
                    {
                        0: {
                            "id": str(uuid.uuid4().hex),
                            "edition": "Inserted Book Edition",
                            "title": "Inserted Book Title",
                            "author_ref_id": shared_uuid_author_book_relation,
                        }
                    },
                ],
                "both",
                "upsert",
                "ok",
            ),
        ],
    )
    def test_update(
        self,
        case_name,
        remove_columns,
        modify_rows,
        model_scope,
        update_mode,
        expected_status,
        is_temp_table,
        is_lazy,
        is_validate,
    ):
        # 1. Seed initial data
        df_dict = self.mo_mock_model_frms(
            models=[self._author_model, self._book_model],
            counts=[3, 1],
        )
        if is_lazy:
            df_dict = _convert_to_lazy_dict(df_dict)
        created_status, created_valid_dfs, _ = mo_crud_kit.create(df_dict)
        if is_lazy:
            created_valid_dfs = mo_polars_kit.collect_model_frms(
                created_valid_dfs, streaming=True
            )
        assert created_status == "ok"

        # 2. Prepare update payload
        update_dict = self.mo_update_mock_model_frms(
            created_valid_dfs,
            exclude_columns=remove_columns,
            modify=modify_rows,
            counts=[3, 1, 1],
        )
        scopes = {"main": slice(0, 1), "sub": slice(1, 2), "both": slice(None)}
        update_dict = dict(list(update_dict.items())[scopes[model_scope]])
        if is_lazy:
            update_dict = _convert_to_lazy_dict(update_dict)

        # 3. Run update
        if expected_status == "raise":
            with pytest.raises(ValueError):
                mo_crud_kit.update(
                    update_dict, is_temp_table=is_temp_table, is_validate=is_validate
                )
            return

        updated_status, updated_valid_dfs, updated_invalid_dfs = mo_crud_kit.update(
            update_dict, is_temp_table=is_temp_table, is_validate=is_validate
        )
        if is_lazy:
            updated_valid_dfs = mo_polars_kit.collect_model_frms(
                updated_valid_dfs, streaming=True
            )
            updated_invalid_dfs = mo_polars_kit.collect_model_frms(
                updated_invalid_dfs, streaming=True
            )

        # 4. Assertions
        assert case_name is not None
        assert updated_status == expected_status
        for df in updated_valid_dfs.values():
            assert "__error__info" not in df.columns
        for df in updated_invalid_dfs.values():
            assert "__error__info" in df.columns
        if expected_status == "ok":
            assert not mo_polars_kit.is_model_frms_empty(updated_valid_dfs)
            assert mo_polars_kit.is_model_frms_empty(updated_invalid_dfs)
            _assert_db_matches(updated_valid_dfs)

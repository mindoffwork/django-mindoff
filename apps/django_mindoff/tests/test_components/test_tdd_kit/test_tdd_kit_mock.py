import copy

import pytest
from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models import ForeignKey
from ....components.tdd_kit import MindoffTestCase

# ------------------------
# ⚓ CONSTANTS
# ------------------------
FIELDS = {
    "name": models.CharField(max_length=50),
    "nickname": models.CharField(max_length=50, null=True, blank=True),
    "description": models.TextField(null=True, blank=True),
}

snake_case_regex = r"^[a-z0-9_]+$"
pascal_case_regex = r"^[A-Z][a-zA-Z0-9]+$"


# =================================================================
#  🚂 TestMockApp
# =================================================================
class TestMockApp(MindoffTestCase):
    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_auto_app_creation_unique_names(self):
        """Auto-generated names are unique and well-formed."""
        app1 = self.mo_mock_app()
        app2 = self.mo_mock_app()
        self.asserts.assertNotEqual(app1, app2)
        self._common_assertions(app1)
        self._common_assertions(app2)

    def test_defined_app_creation_and_mixed_environment(self):
        """Named and auto apps coexist without collision."""
        auto_app = self.mo_mock_app()
        defined_app1 = self.mo_mock_app(app_name="custom_app")
        defined_app2 = self.mo_mock_app(app_name="customapp")
        self.asserts.assertIn("custom_app", apps.app_configs)
        self._common_assertions(auto_app)
        self._common_assertions(defined_app1)
        self._common_assertions(defined_app2)

    def _common_assertions(self, app_name):
        models_module = __import__(f"{app_name}.models")
        self.asserts.assertTrue(hasattr(models_module, "models"))
        self.asserts.assertRegex(app_name, snake_case_regex)
        self.asserts.assertTrue(app_name.islower())
        self.asserts.assertIn(app_name, apps.app_configs)

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_invalid_app_name(self):
        """Special chars, leading digits, dots, and path separators all raise."""
        bad_names = [
            "invalid@app",
            "123startdigit",
            "apps.app_name",
            "apps/app_name/evil",
        ]
        for name in bad_names:
            with self.asserts.assertRaises(Exception):
                self.mo_mock_app(app_name=name)

    def test_app_name_collision(self):
        """Duplicate name within session raises ValueError."""
        _ = self.mo_mock_app(app_name="duplicate_app")
        with self.asserts.assertRaises(ValueError):
            self.mo_mock_app(app_name="duplicate_app")

    # 🚧 BOUNDARY ────────────────────────────────────────────────────────

    def test_minimum_length_name(self):
        name = self.mo_mock_app("a")
        self.asserts.assertIn(name, apps.app_configs)

    # 🌀 ANOMALY ──────────────────────────────────────────────────────────

    def test_case_sensitivity_normalization(self):
        app1 = self.mo_mock_app("MixedCaseApp")
        app2 = self.mo_mock_app("Mixed Case app")
        app3 = self.mo_mock_app("mixedCase App")
        self.asserts.assertEqual(app1, "mixedcaseapp")
        self.asserts.assertEqual(app2, "mixed_case_app")
        self.asserts.assertEqual(app3, "mixedcase_app")

    def test_empty_string_name_fallbacks_to_auto(self):
        name = self.mo_mock_app("")
        assert name != ""
        assert len(name) > 0


# =================================================================
#  🚂 TestMockModel
# =================================================================
@pytest.mark.django_db(transaction=True)
class TestMockModel(MindoffTestCase):
    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_defined_and_auto_model_creation_together(self):
        """Defined + auto models coexist with unique names and table names."""
        defined_model_1 = self.mo_mock_model(model_name="TestModel")
        defined_model_2 = self.mo_mock_model(model_name="Test2Model")
        auto_model_1 = self.mo_mock_model()
        auto_model_2 = self.mo_mock_model()
        model_names = {
            defined_model_1.__name__,
            defined_model_2.__name__,
            auto_model_1.__name__,
            auto_model_2.__name__,
        }
        table_names = {
            defined_model_1._meta.db_table,
            defined_model_2._meta.db_table,
            auto_model_1._meta.db_table,
            auto_model_2._meta.db_table,
        }
        self.asserts.assertEqual(defined_model_1.__name__, "TestModel")
        self.asserts.assertEqual(defined_model_2.__name__, "Test2Model")
        self.asserts.assertEqual(len(model_names), 4)
        self.asserts.assertEqual(len(table_names), 4)
        for m in [defined_model_1, defined_model_2, auto_model_1, auto_model_2]:
            self._common_assertions(m)

    def test_foreign_key_addon(self):
        """Multiple FK fields are created with correct db_column conventions."""
        self.mo_mock_app(app_name="temp_otherapp")
        self.mo_mock_app(app_name="temp_app")

        temp_other_auto = self.mo_mock_model(app_name="temp_otherapp")
        temp_other_defined = self.mo_mock_model(
            app_name="temp_app", model_name="DefinedOtherModel"
        )
        perm_same_app = apps.get_model("auth", "User")
        perm_other_app = apps.get_model("contenttypes", "ContentType")

        model = self.mo_mock_model(
            foreign_keys=[
                (temp_other_auto._meta.app_label, temp_other_auto.__name__),
                (temp_other_defined._meta.app_label, temp_other_defined.__name__),
                (perm_same_app._meta.app_label, perm_same_app.__name__),
                (perm_other_app._meta.app_label, perm_other_app.__name__),
            ]
        )

        fk_fields = [
            f for f in model._meta.concrete_fields if isinstance(f, models.ForeignKey)
        ]

        self.asserts.assertEqual(len(fk_fields), 4)
        linked_models = {fk.related_model for fk in fk_fields}
        self.asserts.assertEqual(
            linked_models,
            {temp_other_auto, temp_other_defined, perm_same_app, perm_other_app},
        )

        for fk in fk_fields:
            actual_db_column = fk.db_column or fk.get_attname_column()[1]
            self.asserts.assertTrue(actual_db_column.endswith("_ref_id"))
            pk_field = fk.related_model._meta.pk
            self.asserts.assertNotEqual(
                actual_db_column, pk_field.db_column or pk_field.attname
            )

        self.asserts.assertEqual(model._meta.pk.db_column, "id")
        self._common_assertions(model)

    def test_fields_addon_preserves_attributes(self):
        """Field attributes (max_length, null, blank, default, db_column, unique, help_text) are preserved."""
        fields = {
            "char_field": models.CharField(max_length=50, help_text="A short string"),
            "int_field": models.IntegerField(help_text="An integer field"),
            "char_field_parameters": models.CharField(
                max_length=100,
                null=True,
                blank=True,
                default="default text",
                unique=True,
                db_column="char_col",
            ),
            "int_field_parameters": models.IntegerField(
                null=True, blank=True, default=10, unique=True, db_column="int_column"
            ),
            "char255": models.CharField(max_length=255),
        }
        model = self.mo_mock_model(fields=fields)
        attrs_to_check = [
            "max_length",
            "null",
            "blank",
            "default",
            "db_column",
            "unique",
            "editable",
            "primary_key",
            "help_text",
            "verbose_name",
            "choices",
            "max_digits",
            "decimal_places",
        ]
        for field_name, field_obj in fields.items():
            model_field = model._meta.get_field(field_name)
            self.asserts.assertIsInstance(model_field, type(field_obj))
            for attr in attrs_to_check:
                if hasattr(field_obj, attr):
                    self.asserts.assertEqual(
                        getattr(model_field, attr, None),
                        getattr(field_obj, attr),
                        msg=f"Field '{field_name}' attr '{attr}' mismatch",
                    )
        self._common_assertions(model)

    def _common_assertions(self, model):
        app_label = model._meta.app_label
        self.asserts.assertIsNotNone(app_label)
        self.asserts.assertNotEqual(app_label, "")
        self.asserts.assertRegex(model._meta.db_table, snake_case_regex)
        self.asserts.assertRegex(model.__name__, pascal_case_regex)
        for field in model._meta.concrete_fields:
            if hasattr(field, "db_column") and field.db_column:
                self.asserts.assertRegex(model._meta.db_table, snake_case_regex)

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_string_naming_errors(self):
        """Invalid model_name and table_name values raise AssertionError."""
        with pytest.raises(AssertionError):
            self.mo_mock_model(model_name="notPascalModel")
        with pytest.raises(AssertionError):
            self.mo_mock_model(table_name="NotSnakeCase")
        with pytest.raises(AssertionError):
            self.mo_mock_model(model_name="Test")
        for name in ["123Model", "My Model", "Model$", "Model!"]:
            with pytest.raises(AssertionError):
                self.mo_mock_model(model_name=name)

    def test_existential_crisis_errors(self):
        """Non-existent app, duplicate model name, and missing FK target all raise."""
        with pytest.raises(ValueError):
            self.mo_mock_model(app_name="nonexistentapp")
        self.mo_mock_model(model_name="DuplicateModel")
        with pytest.raises(ValueError):
            self.mo_mock_model(model_name="DuplicateModel")
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("nonexistentapp", "NonexistentModel")])

    def test_fk_model_string_path_invalid(self):
        app_name = self.mo_mock_app()
        self.mo_mock_model(model_name="DuplicateModel", app_name=app_name)
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("directory_temp_app", "DuplicateModel")])

    def test_field_related_errors(self):
        """Duplicate field keys → last wins; unsupported field param → TypeError."""
        model_class = self.mo_mock_model(
            fields={
                "field_1": models.CharField(max_length=10),
                **{"field_1": models.IntegerField()},
            }
        )
        field_1_fields = [
            f for f in model_class._meta.concrete_fields if f.name == "field_1"
        ]
        self.asserts.assertEqual(len(field_1_fields), 1)
        self.asserts.assertIsInstance(field_1_fields[0], models.IntegerField)

        class BadField(models.CharField):
            def __init__(self, *args, **kwargs):
                kwargs["nonexistent_param"] = True
                super().__init__(*args, **kwargs)

        with pytest.raises(TypeError):
            self.mo_mock_model(fields={"bad_field": BadField(max_length=10)})

    # 🚧 BOUNDARY ────────────────────────────────────────────────────────

    def test_custom_model_table_name_at_max_length(self):
        """Explicit and auto-derived table names respect DB_TABLE_NAME_MAX_LENGTH."""
        max_length = getattr(settings, "DB_TABLE_NAME_MAX_LENGTH", 63)

        model1 = self.mo_mock_model(model_name="TestModel", table_name="a" * max_length)
        self.asserts.assertEqual(len(model1._meta.db_table) - 4, max_length)

        model2 = self.mo_mock_model(model_name="B" * 63 + "Model")
        self.asserts.assertEqual(len(model2._meta.db_table) - 4, max_length)

    def test_dynamic_creator_allows_multiple_fk_fields(self):
        """Four FK fields can be created without errors."""
        fk_models = []
        for i in range(4):
            temp = self.mo_mock_model(model_name=f"TempModel{i}Model")
            fk_models.append((temp._meta.app_label, temp.__name__))
        model = self.mo_mock_model(foreign_keys=fk_models)
        fk_fields = [
            f for f in model._meta.concrete_fields if isinstance(f, models.ForeignKey)
        ]
        self.asserts.assertEqual(len(fk_fields), 4)

    # 🌀 ANOMALY ──────────────────────────────────────────────────────────

    def test_app_name_empty_autofills_current_app(self):
        model = self.mo_mock_model(app_name="")
        self._common_assertions(model)


# =================================================================
#  🚂 TestMockModelFrms
# =================================================================
@pytest.mark.django_db(transaction=True)
class TestMockModelFrms(MindoffTestCase):
    # fmt: off
    @pytest.mark.parametrize(
        "model_info, counts, expected_df_counts",
        [
            # 1. Single model, no FK
            (
                [{"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}],
                [2], [2],
            ),
            # 2. Parent → Child (2-model single-FK chain)
            (
                [
                    {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "ChildModel",  "fields": copy.deepcopy(FIELDS), "fk": [("ParentModel",)]},
                ],
                [2, 1], [2, 2],
            ),
            # 3. Parent → Child → Grandchild (3-model chain)
            (
                [
                    {"name": "ParentModel",     "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "ChildModel",      "fields": copy.deepcopy(FIELDS), "fk": [("ParentModel",)]},
                    {"name": "GrandchildModel", "fields": copy.deepcopy(FIELDS), "fk": [("ChildModel",)]},
                ],
                [2, 1, 1], [2, 2, 2],
            ),
            # 4. Two root models, no FK between them
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                ],
                [2, 2], [2, 2],
            ),
            # 5. Two roots, one child off root 1
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Child11Model", "fields": copy.deepcopy(FIELDS), "fk": [("Parent1Model",)]},
                ],
                [2, 2, 1], [2, 2, 2],
            ),
        ],
    )
    # fmt: on
    def test_mock_model_frms_structural(self, model_info, counts, expected_df_counts):
        """FK chain wiring, row counts, and FK integrity across topologies."""
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(models=models_list, counts=counts)

        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns=None)
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows=[])
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_removed(self):
        """Column exclusion across all 4 model indexes."""
        model_info = [
            {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
            {
                "name": "ChildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ParentModel",)],
            },
            {
                "name": "GrandchildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ChildModel",)],
            },
            {
                "name": "GreatGrandchildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("GrandchildModel",)],
            },
        ]
        exclude_columns = [["nickname"], ["description"], [], ["nickname"]]
        counts = [2, 1, 1, 1]
        expected_df_counts = [2, 2, 2, 2]

        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=counts, exclude_columns=exclude_columns
        )
        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns[idx])
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows=[])
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_modified(self):
        """Row modification across both modify indexes."""
        model_info = [
            {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
            {
                "name": "ChildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ParentModel",)],
            },
        ]
        modify_rows = [
            {0: {"description": "modified1"}},
            {1: {"description": "modified2"}},
        ]
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=[2, 1], modify=modify_rows
        )
        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns=None)
            _validate_rows(df, idx, [2, 2][idx], modify_rows)
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_modified_removed(self):
        """Combined column exclusion + row modification across all indexes."""
        model_info = [
            {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
            {
                "name": "ChildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ParentModel",)],
            },
            {
                "name": "GrandchildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ChildModel",)],
            },
            {
                "name": "GreatGrandchildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("GrandchildModel",)],
            },
        ]
        exclude_columns = [["nickname"], ["description"], [], ["nickname"]]
        modify_rows = [{}, {0: {"name": "modified3"}}]
        counts = [2, 1, 1, 1]
        expected_df_counts = [2, 2, 2, 2]

        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list,
            counts=counts,
            exclude_columns=exclude_columns,
            modify=modify_rows,
        )
        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns[idx])
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows)
            _validate_foreign_keys(df_dict)

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "exclude_columns, modify_rows, expected_error",
        [
            ([["non_existing_col"]], [], ValueError),
            ([], [{0: {"non_existing_col": "modified"}}], ValueError),
            (
                [["non_existing_col"]],
                [{0: {"non_existing_col": "modified"}}],
                ValueError,
            ),
            ([["non_existing_col"]], [{4: {"name": "modified"}}], ValueError),
        ],
    )
    def test_mock_model_frms_rejections(
        self, exclude_columns, modify_rows, expected_error
    ):
        models_list = self._create_models(
            [{"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}]
        )
        with pytest.raises(expected_error):
            self.mo_mock_model_frms(
                models=models_list, exclude_columns=exclude_columns, modify=modify_rows
            )

    def _create_models(self, models_info):
        app_name = self.mo_mock_app()
        created_models_list = []
        created_models_dict = {}
        for node in models_info:
            fk_resolved = [
                (app_name, created_models_dict[fk_name].__name__)
                for fk_name, in node["fk"]
            ]
            model_cls = self.mo_mock_model(
                model_name=node["name"],
                app_name=app_name,
                fields=node["fields"],
                foreign_keys=fk_resolved,
            )
            created_models_dict[node["name"]] = model_cls
            created_models_list.append(model_cls)
        return created_models_list


# =================================================================
#  🚂 TestUpdateMockModelFrms
# =================================================================
@pytest.mark.django_db(transaction=True)
class TestUpdateMockModelFrms(MindoffTestCase):
    """Tests for the mo_update_mock_model_frms fixture."""

    def _base_df_dict(self, model_info=None, counts=None):
        """Helper: build an initial df_dict from a simple 2-model chain."""
        if model_info is None:
            model_info = [
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
                {
                    "name": "ChildModel",
                    "fields": copy.deepcopy(FIELDS),
                    "fk": [("ParentModel",)],
                },
            ]
        if counts is None:
            counts = [2, 1]
        app_name = self.mo_mock_app()
        created = {}
        models_list = []
        for node in model_info:
            fk_resolved = [
                (app_name, created[fk_name].__name__) for fk_name, in node["fk"]
            ]
            m = self.mo_mock_model(
                model_name=node["name"],
                app_name=app_name,
                fields=node["fields"],
                foreign_keys=fk_resolved,
            )
            created[node["name"]] = m
            models_list.append(m)
        return self.mo_mock_model_frms(models=models_list, counts=counts)

    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_non_key_columns_are_updated(self):
        """Non-PK, non-FK columns receive new generated values."""
        df_dict = self._base_df_dict()
        _ = {model: df["name"].to_list() for model, df in df_dict.items()}
        updated = self.mo_update_mock_model_frms(df_dict)
        for _, df in updated.items():
            assert "name" in df.columns

    def test_pk_and_fk_columns_are_preserved(self):
        """PK and FK values are never replaced during update."""
        df_dict = self._base_df_dict()
        original_pks = {
            model: df[model._meta.pk.db_column or model._meta.pk.attname].to_list()
            for model, df in df_dict.items()
        }
        updated = self.mo_update_mock_model_frms(df_dict)
        for model, df in updated.items():
            pk_col = model._meta.pk.db_column or model._meta.pk.attname
            assert (
                df[pk_col].to_list() == original_pks[model]
            ), f"PK column '{pk_col}' should not change during update"

    def test_keep_columns_are_preserved(self):
        """Columns listed in keep_columns survive the update unchanged."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        models_list = list(df_dict.keys())
        original_name_vals = df_dict[models_list[0]]["name"].to_list()

        updated = self.mo_update_mock_model_frms(df_dict, keep_columns=[["name"]])
        assert updated[models_list[0]]["name"].to_list() == original_name_vals

    def test_modify_applies_to_updated_df(self):
        """modify= overrides specific cells in the updated DataFrame."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        updated = self.mo_update_mock_model_frms(
            df_dict, modify=[{0: {"description": "overridden"}}]
        )
        model = list(updated.keys())[0]
        assert updated[model][0, "description"] == "overridden"

    def test_exclude_columns_removes_columns(self):
        """exclude_columns drops the named columns from the updated DataFrame."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        updated = self.mo_update_mock_model_frms(
            df_dict, exclude_columns=[["nickname"]]
        )
        model = list(updated.keys())[0]
        assert "nickname" not in updated[model].columns

    def test_output_has_same_model_keys(self):
        """The returned dict has the same model keys as the input."""
        df_dict = self._base_df_dict()
        updated = self.mo_update_mock_model_frms(df_dict)
        assert set(updated.keys()) == set(df_dict.keys())

    def test_default_counts_generates_one_row_per_model(self):
        """No counts supplied → 1 new object baked per model; existing rows stay."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[3],
        )
        # After update, row count should stay the same (update replaces values, not rows)
        updated = self.mo_update_mock_model_frms(df_dict)
        model = list(updated.keys())[0]
        assert updated[model].height == df_dict[model].height

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_exclude_nonexistent_column_raises(self):
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises(ValueError):
            self.mo_update_mock_model_frms(df_dict, exclude_columns=[["no_such_col"]])

    def test_modify_nonexistent_column_raises(self):
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises(ValueError):
            self.mo_update_mock_model_frms(
                df_dict, modify=[{0: {"no_such_col": "bad"}}]
            )

    def test_modify_out_of_range_row_raises(self):
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises((IndexError, ValueError)):
            self.mo_update_mock_model_frms(df_dict, modify=[{99: {"name": "bad"}}])


# =================================================================
# 🧩 Sub-functions (shared validators)
# =================================================================
def _validate_columns(model, df, exclude_columns):
    for field in model._meta.concrete_fields:
        field_name = field.db_column or field.name
        if exclude_columns:
            for col in exclude_columns:
                assert (
                    col not in df.columns
                ), f"Column '{col}' should have been excluded but is still present"
            if field_name not in exclude_columns:
                assert (
                    field_name in df.columns
                ), f"Column '{field_name}' should be present but is missing"
        else:
            assert (
                field_name in df.columns
            ), f"Column '{field_name}' should be present but is missing"


def _validate_rows(df, idx, expected_count, modify_rows):
    assert expected_count == df.height
    if len(modify_rows) > idx and modify_rows[idx]:
        for row_idx, modified_info in modify_rows[idx].items():
            for col, expected in modified_info.items():
                actual = df[row_idx, col]
                assert (
                    actual == expected
                ), f"Row {row_idx}, col {col}: {actual} != {expected}"


def _validate_foreign_keys(df_dict):
    model_fk_dict = _extract_fk_dict(df_dict)
    _assert_shared_columns_unique(model_fk_dict)


def _extract_fk_dict(df_dict):
    result = {}
    for model, df in df_dict.items():
        model_info = {}
        pk_field = model._meta.pk
        pk_col = pk_field.db_column or pk_field.attname
        if pk_col in df.columns:
            model_info[pk_col] = df[pk_col].to_list()
        for field in model._meta.concrete_fields:
            if isinstance(field, ForeignKey):
                fk_col = field.db_column or field.column
                if fk_col in df.columns:
                    model_info[fk_col] = df[fk_col].to_list()
        result[model] = model_info
    return result


def _assert_shared_columns_unique(fk_data):
    for model, fk_dict in fk_data.items():
        for fk_col, fk_list in fk_dict.items():
            current_fk_list = set(fk_list)
            for iter_model, iter_fk_dict in fk_data.items():
                for iter_fk_col, iter_fk_list in fk_dict.items():
                    if fk_col == iter_fk_col:
                        target_fk_list = set(iter_fk_list)
                        assert current_fk_list == target_fk_list, (
                            f"{fk_col} does not match between "
                            f"{model.__name__} and {iter_model.__name__}"
                        )

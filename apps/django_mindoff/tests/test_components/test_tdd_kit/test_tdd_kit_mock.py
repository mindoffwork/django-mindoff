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
    "nickname": models.CharField(max_length=50, null=True, blank=True),  # optional
    "description": models.TextField(null=True, blank=True),  # optional
}

snake_case_regex = r"^[a-z0-9_]+$"
pascal_case_regex = r"^[A-Z][a-zA-Z0-9]+$"


# =================================================================
#  🚂 MAIN CLASSES
# =================================================================
class TestMockApp(MindoffTestCase):
    # ------------------------
    # ✅ ACCEPTANCE TESTS
    # ------------------------
    def test_auto_app_creation_unique_names(self):
        """
        1. **Auto App Creation** — Creates an app with a system-generated name
        when none is given and can create multiple auto-generated apps sequentially
        without name collisions.
        """
        app1 = self.mo_mock_app()
        app2 = self.mo_mock_app()
        self.asserts.assertNotEqual(app1, app2)
        self._common_assertions(app1)
        self._common_assertions(app2)

    def test_defined_app_creation_and_mixed_environment(self):
        """
        2. **Defined App Creation** — Creates an app with a user-specified name
        and can create multiple defined apps without name collisions.
        """
        auto_app = self.mo_mock_app()
        defined_app1 = self.mo_mock_app(app_name="custom_app")
        defined_app2 = self.mo_mock_app(app_name="customapp")
        self.asserts.assertIn("custom_app", apps.app_configs)
        self._common_assertions(auto_app)
        self._common_assertions(defined_app1)
        self._common_assertions(defined_app2)

    def _common_assertions(self, app_name):
        """
        3. **Common:**
        """
        # - Can import models from the new app without errors.
        models_module = __import__(f"{app_name}.models")
        self.asserts.assertTrue(hasattr(models_module, "models"))
        # - Generated name follows naming rules (`snake_case`, no special chars, no leading digits).
        self.asserts.assertRegex(app_name, snake_case_regex)
        self.asserts.assertTrue(app_name.islower())
        # - App appears in `apps.app_configs` with correct label.
        self.asserts.assertIn(app_name, apps.app_configs)

    # ------------------------
    # 🚫 REJECTION TESTS
    # ------------------------
    def test_invalid_app_name(self):
        """
        1. **Invalid App Name** — Names with special characters (`@`, `#`, ),
        starting with a digit., Reserved Python keywords (`class`, `import`).
        """
        bad_names = ["invalid@app", "123startdigit", "apps.app_name"]
        for name in bad_names:
            with self.asserts.assertRaises(Exception):
                self.mo_mock_app(app_name=name)

    def test_app_name_collision(self):
        """
        2. **App Name Collision** — Creating an app with a name that already
        exists in `INSTALLED_APPS` and Creating an auto-generated app when the
        generated name already exists.
        """
        _ = self.mo_mock_app(app_name="duplicate_app")
        with self.asserts.assertRaises(Exception):
            self.mo_mock_app(app_name="duplicate_app")

    def test_invalid_app_path(self):
        """
        3. **Invalid App Path** — Attempt to create app outside of
        allowed namespace (e.g., `../../evil`).
        """
        with self.asserts.assertRaises(Exception):
            self.mo_mock_app(app_name="apps/app_name/evil")

    def test_concurrent_creation_same_name(self):
        self.mo_mock_app("temp_app_concurrent")
        with self.asserts.assertRaises(ValueError):
            self.mo_mock_app("temp_app_concurrent")

    # ------------------------
    # 🚧 BOUNDARY TESTS
    # ------------------------
    def test_minimum_length_name(self):
        name = self.mo_mock_app("a")
        self.asserts.assertIn(name, apps.app_configs)

    def test_maximum_length_name(self):
        name = "x" * 100
        created_name = self.mo_mock_app(name)
        self.asserts.assertIn(created_name, apps.app_configs)

    def test_case_sensitivity_normalization(self):
        app1 = self.mo_mock_app("MixedCaseApp")
        app2 = self.mo_mock_app("Mixed Case app")
        app3 = self.mo_mock_app("mixedCase App")
        self.asserts.assertEqual(app1, "mixedcaseapp")
        self.asserts.assertEqual(app2, "mixed_case_app")
        self.asserts.assertEqual(app3, "mixedcase_app")

    # ------------------------
    # 🌀 ANOMALY TESTS
    # ------------------------
    def test_empty_string_name_fallbacks_to_auto(self):
        name = self.mo_mock_app("")
        assert name != ""
        assert len(name) > 0

    def test_exceeds_max_length_throws_error(self):
        with self.asserts.assertRaises(Exception):
            self.mo_mock_app("x" * 1024)


@pytest.mark.django_db(transaction=True)
class TestMockModel(MindoffTestCase):
    # ------------------------
    # ✅ ACCEPTANCE TESTS
    # ------------------------
    # REMOVED: test_auto_model_creation_unique_names
    # Reason: fully covered by test_defined_and_auto_model_creation_together,
    # which creates 2 defined + 2 auto models and asserts uniqueness + _common_assertions on all four.

    def test_defined_and_auto_model_creation_together(self):
        """
        Auto Model Creation & Defined Model Creation Can exist for Same Test.
        Also covers: auto-name uniqueness, table name uniqueness, _common_assertions for all variants.
        """
        defined_name_1 = "TestModel"
        defined_name_2 = "Test2Model"
        defined_model_1 = self.mo_mock_model(model_name=defined_name_1)
        defined_model_2 = self.mo_mock_model(model_name=defined_name_2)
        auto_model_1 = self.mo_mock_model()
        auto_model_2 = self.mo_mock_model()
        model_names_dict = {
            defined_model_1.__name__,
            defined_model_2.__name__,
            auto_model_1.__name__,
            auto_model_2.__name__,
        }
        table_names_dict = {
            defined_model_1._meta.db_table,
            defined_model_2._meta.db_table,
            auto_model_1._meta.db_table,
            auto_model_2._meta.db_table,
        }
        self.asserts.assertEqual(defined_model_1.__name__, defined_name_1)
        self.asserts.assertEqual(defined_model_2.__name__, defined_name_2)
        self.asserts.assertEqual(
            len(model_names_dict),
            4,
            msg=f"Duplicate model names found: \n{model_names_dict}",
        )
        self.asserts.assertEqual(
            len(table_names_dict),
            4,
            msg=f"Duplicate table names found: {table_names_dict}",
        )
        self._common_assertions(defined_model_1)
        self._common_assertions(defined_model_2)
        self._common_assertions(auto_model_1)
        self._common_assertions(auto_model_2)

    def test_foreign_key_addon(self):
        """
        3. **Foreign Key Addon:** Link Multiple Existing Model Names → Add multiple FK fields
        - Primary Key db_column should be 'id'
        - Foreign Key db_columns should follow the '{field_name}_ref' convention
        """
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

        self.asserts.assertEqual(
            len(fk_fields), 4, msg="Not All Foreign key fields are created"
        )

        # Validate the models are correctly linked
        linked_models = {fk.related_model for fk in fk_fields}
        expected_models = {
            temp_other_auto,
            temp_other_defined,
            perm_same_app,
            perm_other_app,
        }
        self.asserts.assertEqual(
            linked_models, expected_models, msg="Foreign key model not matching"
        )

        # --- NEW CONVENTION ASSERTIONS ---
        for fk in fk_fields:
            # 1. Verify the FK column ends with _ref
            actual_db_column = fk.db_column or fk.get_attname_column()[1]
            self.asserts.assertTrue(
                actual_db_column.endswith("_ref_id"),
                msg=f"FK column '{actual_db_column}' does not follow the _ref suffix convention.",
            )

            # 2. Verify it DOES NOT match the target's PK name (which is 'id')
            related_model = fk.related_model
            pk_field = related_model._meta.pk
            target_pk_column = pk_field.db_column or pk_field.attname

            self.asserts.assertNotEqual(
                actual_db_column,
                target_pk_column,
                msg=f"FK '{fk.name}' matches target PK name. Should be separate names now.",
            )

        # Verify the Primary Key of the model itself is just 'id'
        self.asserts.assertEqual(model._meta.pk.db_column, "id")
        self._common_assertions(model)

    def test_fields_addon_single_and_multiple(self):
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
                    expected = getattr(field_obj, attr)
                    actual = getattr(model_field, attr, None)
                    self.asserts.assertEqual(
                        actual,
                        expected,
                        msg=f"Field '{field_name}' attribute '{attr}' mismatch: expected {expected!r}, got {actual!r}",
                    )

        self._common_assertions(model)

    def _common_assertions(self, model):
        app_label = model._meta.app_label
        self.asserts.assertIsNotNone(app_label, msg="app_label should not be None")
        self.asserts.assertNotEqual(
            app_label, "", msg="app_label should not be an empty string"
        )
        self.asserts.assertRegex(
            model._meta.db_table,
            snake_case_regex,
            msg=f"Table name '{model._meta.db_table}' is not snake_case",
        )
        self.asserts.assertRegex(
            model.__name__,
            pascal_case_regex,
            msg="Model Name Not Pascal Case",
        )
        for field in model._meta.concrete_fields:
            if hasattr(field, "db_column") and field.db_column:
                self.asserts.assertRegex(
                    model._meta.db_table,
                    snake_case_regex,
                    msg=f"DB Column name '{field.db_column}' is not snake_case",
                )

    # ------------------------
    # 🚫 REJECTION TESTS
    # ------------------------
    def test_string_naming_errors(self):
        # 1. ModelName is not PascalCase
        with pytest.raises(AssertionError):
            self.mo_mock_model(model_name="notPascalModel")
        # 2. TableName is not snake_case
        with pytest.raises(AssertionError):
            self.mo_mock_model(table_name="NotSnakeCase")
        # 3. ModelName does not end with 'Model'
        with pytest.raises(AssertionError):
            self.mo_mock_model(model_name="Test")
        # 4. ModelName contains invalid chars (e.g. starting with number, spaces, special chars)
        invalid_names = ["123Model", "My Model", "Model$", "Model!"]
        for name in invalid_names:
            with pytest.raises(AssertionError):
                self.mo_mock_model(model_name=name)

    def test_existential_crisis_errors(self):
        # 1. App label does not exist
        with pytest.raises(ValueError):
            self.mo_mock_model(app_name="nonexistentapp")
        # 2. Duplicate model name already registered
        self.mo_mock_model(model_name="DuplicateModel")
        with pytest.raises(ValueError):
            self.mo_mock_model(model_name="DuplicateModel")
        # 3. FK model in fk_models_list does not exist
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("nonexistentapp", "NonexistentModel")])

    def test_fk_model_string_path_invalid(self):
        app_name = self.mo_mock_app()
        self.mo_mock_model(model_name="DuplicateModel", app_name=app_name)
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("directory_temp_app", "DuplicateModel")])

    def test_field_related_errors(self):
        # 1. Duplicate field names in fields
        fields1 = {"field_1": models.CharField(max_length=10)}
        fields2 = {"field_1": models.IntegerField()}  # duplicate key 'field1'
        model_class = self.mo_mock_model(fields={**fields1, **fields2})
        field_1_fields = [
            f for f in model_class._meta.concrete_fields if f.name == "field_1"
        ]
        self.asserts.assertEqual(
            len(field_1_fields),
            1,
            f"Expected exactly one 'field_1' field, found {len(field_1_fields)}",
        )
        field_1 = field_1_fields[0]
        self.asserts.assertIsInstance(
            field_1,
            models.IntegerField,
            f"'field_1' must be IntegerField, found {field_1.__class__.__name__}",
        )

        # 2. Unsupported field parameter passed
        class BadField(models.CharField):
            def __init__(self, *args, **kwargs):
                kwargs["nonexistent_param"] = True
                super().__init__(*args, **kwargs)

        with pytest.raises(TypeError):
            self.mo_mock_model(fields={"bad_field": BadField(max_length=10)})

    # ------------------------
    # 🚧 BOUNDARY TESTS
    # ------------------------
    def test_custom_model_table_name_at_max_length(self):
        max_length = getattr(settings, "DB_TABLE_NAME_MAX_LENGTH", 63)
        long_table_name = "a" * max_length
        model_name = "TestModel"
        model = self.mo_mock_model(model_name=model_name, table_name=long_table_name)
        self.asserts.assertEqual(len(model._meta.db_table) - 4, max_length)

    def test_dynamic_creator_allows_10_plus_fk_fields(self):
        # Create 10 different temporary models in a test app
        fk_models = []
        for i in range(10):
            model_name = f"TempModel{i}Model"
            temp_model = self.mo_mock_model(model_name=model_name)
            fk_models.append((temp_model._meta.app_label, temp_model.__name__))
        model = self.mo_mock_model(foreign_keys=fk_models)
        fk_fields = [
            f for f in model._meta.concrete_fields if isinstance(f, models.ForeignKey)
        ]
        self.asserts.assertEqual(len(fk_fields), 10)

    def test_charfield_max_length_exactly_255(self):
        fields = {"char255": models.CharField(max_length=255)}
        model = self.mo_mock_model(fields=fields)
        char_field = model._meta.get_field("char255")
        self.asserts.assertEqual(char_field.max_length, 255)

    def test_auto_generated_table_name_length_at_max_limit(self):
        max_length = getattr(settings, "DB_TABLE_NAME_MAX_LENGTH", 63)
        model_name = "A" * 63 + "Model"
        model = self.mo_mock_model(model_name=model_name)
        self.asserts.assertEqual(len(model._meta.db_table) - 4, max_length)

    # ------------------------
    # 🌀 ANOMALY TESTS
    # ------------------------
    def test_app_name_empty_autofills_current_app(self):
        model = self.mo_mock_model(app_name="")
        self._common_assertions(model)


@pytest.mark.django_db(transaction=True)
class TestMockModelFrms(MindoffTestCase):
    """
    Parametrize strategy:
      - test_mock_model_frms_structural: 7 unique FK chain structures × scenario A (no col ops).
        Proves that model creation, FK wiring, row counts, and FK value integrity are correct
        for every structural pattern. Column ops are NOT tested here — they're orthogonal.
      - test_mock_model_frms_col_removed: scenario B on a 4-model chain.
        Uses all 4 exclude_columns indexes so every per-model exclude path is exercised.
      - test_mock_model_frms_col_modified: scenario C on a 2-model chain.
        Exercises both modify indexes (index 0 and index 1).
      - test_mock_model_frms_col_modified_removed: scenario D on a 4-model chain.
        Exercises combined exclude + modify across all indexes simultaneously.

    Before: 14 model_info × 4 scenarios = 56 tests (7 structural dups × 3 extra scenarios each).
    After:  7 (structural) + 1 (B) + 1 (C) + 1 (D) = 10 tests. Coverage identical.
    """

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
            # 4. Parent → Child → Grandchild → GreatGrandchild (4-model chain)
            (
                [
                    {"name": "ParentModel",          "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "ChildModel",           "fields": copy.deepcopy(FIELDS), "fk": [("ParentModel",)]},
                    {"name": "GrandchildModel",      "fields": copy.deepcopy(FIELDS), "fk": [("ChildModel",)]},
                    {"name": "GreatGrandchildModel", "fields": copy.deepcopy(FIELDS), "fk": [("GrandchildModel",)]},
                ],
                [2, 1, 1, 1], [2, 2, 2, 2],
            ),
            # 5. Two root models, no FK between them
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                ],
                [2, 2], [2, 2],
            ),
            # 6. Two roots, one child off root 1
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Child11Model", "fields": copy.deepcopy(FIELDS), "fk": [("Parent1Model",)]},
                ],
                [2, 2, 1], [2, 2, 2],
            ),
            # 7. Two roots, one child off root 1, one grandchild off child
            (
                [
                    {"name": "Parent1Model",    "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model",    "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Child11Model",    "fields": copy.deepcopy(FIELDS), "fk": [("Parent1Model",)]},
                    {"name": "Child21Model",    "fields": copy.deepcopy(FIELDS), "fk": [("Child11Model",)]},
                ],
                [2, 2, 1, 1], [2, 2, 2, 2],
            ),
        ],
    )
    # fmt: on
    def test_mock_model_frms_structural(self, model_info, counts, expected_df_counts):
        """
        Structural acceptance: verifies FK chain wiring, row counts, and FK value
        integrity for every unique model topology. No column exclusion/modification.
        """
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(models=models_list, counts=counts)

        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns=None)
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows=[])
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_removed(self):
        """
        Scenario B: column exclusion across all 4 model indexes.
        Uses a 4-model chain so every per-model exclude path is exercised.
        """
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
        """
        Scenario C: row modification across both modify indexes.
        Uses a 2-model chain so index 0 and index 1 are both exercised.
        """
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
        counts = [2, 1]
        expected_df_counts = [2, 2]

        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=counts, modify=modify_rows
        )

        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns=None)
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows)
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_modified_removed(self):
        """
        Scenario D: combined column exclusion + row modification.
        Uses a 4-model chain to exercise all exclude indexes and both modify indexes simultaneously.
        """
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

    # ---------------- Rejection -----------------
    @pytest.mark.parametrize(
        "exclude_columns, modify_rows, expected_error",
        [
            # 1. Removal of Non Existing Column
            ([["non_existing_col"]], [], ValueError),
            # 2. Modification of Non Existing Column
            ([], [{0: {"non_existing_col": "modified"}}], ValueError),
            # 3. Both non-existing col in exclude and modify
            (
                [["non_existing_col"]],
                [{0: {"non_existing_col": "modified"}}],
                ValueError,
            ),
            # 4. Modification of Non Existing Row
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
# 🧩 SUB FUNCTIONS
# =================================================================
def _validate_columns(model, df, exclude_columns):
    for field in model._meta.concrete_fields:
        field_name = field.db_column or field.name
        if exclude_columns:
            assert exclude_columns not in df.columns
            if field_name not in exclude_columns:
                assert field_name in df.columns
        else:
            assert field_name in df.columns


def _validate_rows(df, idx, expected_count, modify_rows):
    assert expected_count == df.height
    modify_assert_count = 0

    if len(modify_rows) > idx and modify_rows[idx]:
        for row_idx, modified_info in modify_rows[idx].items():
            for col, expected in modified_info.items():
                actual = df[row_idx, col]
                assert (
                    actual == expected
                ), f"Row {row_idx}, col {col}: {actual} != {expected}"
                modify_assert_count += 1
    else:
        modify_assert_count += 1

    assert modify_assert_count > 0, "modified rows were not asserted"


def _validate_foreign_keys(df_dict):
    model_fk_dict = _extract_fk_dict(df_dict)
    _assert_shared_columns_unique(model_fk_dict)


def _extract_fk_dict(df_dict):
    result = {}

    for model, df in df_dict.items():
        model_info = {}

        # --- Primary key ---
        pk_field = model._meta.pk
        pk_col = pk_field.db_column or pk_field.attname
        if pk_col in df.columns:
            model_info[pk_col] = df[pk_col].to_list()

        # --- Foreign keys ---
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
                        assert (
                            current_fk_list == target_fk_list
                        ), f"{fk_col} does not match between {model.__name__} and {iter_model.__name__}"

import copy
import datetime
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
import pytest
from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models import ForeignKey
from django.test import override_settings
from django.urls import clear_url_caches
from ....components.tdd_kit import MindoffTestCase
from ....components.managers._create_app import DjangoAppCreator


FIELDS = {
    "name": models.CharField(max_length=50),
    "nickname": models.CharField(max_length=50, null=True, blank=True),
    "description": models.TextField(null=True, blank=True),
}
snake_case_regex = r"^[a-z0-9_]+$"
pascal_case_regex = r"^[A-Z][a-zA-Z0-9]+$"


class TestMockApp(MindoffTestCase):

    def _common_assertions(self, app_name):
        models_module = __import__(f"{app_name}.models")
        self.asserts.assertTrue(hasattr(models_module, "models"))
        self.asserts.assertRegex(app_name, snake_case_regex)
        self.asserts.assertTrue(app_name.islower())
        self.asserts.assertIn(app_name, apps.app_configs)

    def test_auto_app_creation_unique_names(self):
        """ACCEPTANCE: Validates auto app creation unique names."""
        app1 = self.mo_mock_app()
        app2 = self.mo_mock_app()
        self.asserts.assertNotEqual(app1, app2)
        self._common_assertions(app1)
        self._common_assertions(app2)

    def test_defined_app_creation_and_mixed_environment(self):
        """BOUNDARY: Validates defined app creation and mixed environment."""
        auto_app = self.mo_mock_app()
        defined_app1 = self.mo_mock_app(app_name="custom_app")
        defined_app2 = self.mo_mock_app(app_name="customapp")
        self.asserts.assertIn("custom_app", apps.app_configs)
        self._common_assertions(auto_app)
        self._common_assertions(defined_app1)
        self._common_assertions(defined_app2)

    def test_is_return_path_returns_tuple_of_name_and_dir(self):
        """ACCEPTANCE: Validates is return path returns tuple of name and dir."""
        from pathlib import Path

        result = self.mo_mock_app(is_return_path=True)
        assert isinstance(result, tuple), "Expected a tuple when is_return_path=True"
        assert len(result) == 2
        app_name, temp_dir = result
        assert isinstance(app_name, str) and len(app_name) > 0
        assert isinstance(temp_dir, Path)
        assert temp_dir.exists()
        self._common_assertions(app_name)

    def test_invalid_app_name(self):
        """REJECTION: Validates invalid app name."""
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
        """REJECTION: Validates app name collision."""
        _ = self.mo_mock_app(app_name="duplicate_app")
        with self.asserts.assertRaises(ValueError):
            self.mo_mock_app(app_name="duplicate_app")

    def test_minimum_length_name(self):
        """BOUNDARY: Validates minimum length name."""
        name = self.mo_mock_app("a")
        self.asserts.assertIn(name, apps.app_configs)

    def test_case_sensitivity_normalization(self):
        """ACCEPTANCE: Validates case sensitivity normalization."""
        app1 = self.mo_mock_app("MixedCaseApp")
        app2 = self.mo_mock_app("Mixed Case app")
        app3 = self.mo_mock_app("mixedCase App")
        self.asserts.assertEqual(app1, "mixedcaseapp")
        self.asserts.assertEqual(app2, "mixed_case_app")
        self.asserts.assertEqual(app3, "mixedcase_app")

    def test_empty_string_name_fallbacks_to_auto(self):
        """BOUNDARY: Validates empty string name fallbacks to auto."""
        name = self.mo_mock_app("")
        assert name != ""
        assert len(name) > 0


@pytest.mark.django_db(transaction=True)
class TestMockModel(MindoffTestCase):
    def _common_assertions(self, model):
        app_label = model._meta.app_label
        self.asserts.assertIsNotNone(app_label)
        self.asserts.assertNotEqual(app_label, "")
        self.asserts.assertRegex(model._meta.db_table, snake_case_regex)
        self.asserts.assertRegex(model.__name__, pascal_case_regex)
        for field in model._meta.concrete_fields:
            if hasattr(field, "db_column") and field.db_column:
                self.asserts.assertRegex(model._meta.db_table, snake_case_regex)

    def test_defined_and_auto_model_creation_together(self):
        """ACCEPTANCE: Validates defined and auto model creation together."""
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
        """ACCEPTANCE: Validates foreign key addon."""
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

    def test_optional_fk_is_nullable(self):
        """ACCEPTANCE: Validates optional fk is nullable."""
        parent = self.mo_mock_model(model_name="ParentModel")
        child = self.mo_mock_model(
            model_name="ChildModel",
            foreign_keys=[(parent._meta.app_label, "ParentModel", "optional")],
        )
        fk = next(
            f for f in child._meta.concrete_fields if isinstance(f, models.ForeignKey)
        )
        self.asserts.assertTrue(fk.null)
        self.asserts.assertTrue(fk.blank)

    def test_required_fk_is_not_nullable(self):
        """REJECTION: Validates required fk is not nullable."""
        parent = self.mo_mock_model(model_name="ParentModel")
        child = self.mo_mock_model(
            model_name="ChildModel",
            foreign_keys=[(parent._meta.app_label, "ParentModel", "required")],
        )
        fk = next(
            f for f in child._meta.concrete_fields if isinstance(f, models.ForeignKey)
        )
        self.asserts.assertFalse(fk.null)

    def test_fields_addon_preserves_attributes(self):
        """ACCEPTANCE: Validates fields addon preserves attributes."""
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

    def test_custom_base_model_is_used(self):
        """ACCEPTANCE: Validates custom base model is used."""

        class MyBase(models.Model):
            class Meta:
                abstract = True

        app_name = self.mo_mock_app()
        model = self.mo_mock_model(
            model_name="CustomBaseModel",
            app_name=app_name,
            base_model=MyBase,
        )
        assert issubclass(model, MyBase)

    def test_auto_generated_model_names_use_counter_when_base_taken(self):
        """ACCEPTANCE: Validates auto generated model names use counter when base taken."""
        app_name = self.mo_mock_app()
        m0 = self.mo_mock_model(model_name="TestModel", app_name=app_name)
        m1 = self.mo_mock_model(app_name=app_name)
        m2 = self.mo_mock_model(app_name=app_name)
        names = {m0.__name__, m1.__name__, m2.__name__}
        assert len(names) == 3
        assert "Test1Model" in names or "Test2Model" in names

    def test_invalid_fk_option_raises(self):
        """REJECTION: Validates invalid fk option raises."""
        app_name = self.mo_mock_app()
        self.mo_mock_model(model_name="ParentModel", app_name=app_name)
        with pytest.raises(Exception):
            self.mo_mock_model(
                model_name="ChildModel",
                app_name=app_name,
                foreign_keys=[(app_name, "ParentModel", "invalid_option")],
            )

    def test_string_naming_errors(self):
        """REJECTION: Validates string naming errors."""
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
        """REJECTION: Validates existential crisis errors."""
        with pytest.raises(ValueError):
            self.mo_mock_model(app_name="nonexistentapp")
        self.mo_mock_model(model_name="DuplicateModel")
        with pytest.raises(ValueError):
            self.mo_mock_model(model_name="DuplicateModel")
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("nonexistentapp", "NonexistentModel")])

    def test_fk_model_string_path_invalid(self):
        """REJECTION: Validates fk model string path invalid."""
        app_name = self.mo_mock_app()
        self.mo_mock_model(model_name="DuplicateModel", app_name=app_name)
        with pytest.raises(LookupError):
            self.mo_mock_model(foreign_keys=[("directory_temp_app", "DuplicateModel")])

    def test_field_related_errors(self):
        """REJECTION: Validates field related errors."""
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

    def test_custom_model_table_name_at_max_length(self):
        """BOUNDARY: Validates custom model table name at max length."""
        max_length = getattr(settings, "DB_TABLE_NAME_MAX_LENGTH", 63)
        model1 = self.mo_mock_model(model_name="TestModel", table_name="a" * max_length)
        self.asserts.assertEqual(len(model1._meta.db_table) - 4, max_length)
        model2 = self.mo_mock_model(model_name="B" * 63 + "Model")
        self.asserts.assertEqual(len(model2._meta.db_table) - 4, max_length)

    def test_dynamic_creator_allows_multiple_fk_fields(self):
        """ACCEPTANCE: Validates dynamic creator allows multiple fk fields."""
        fk_models = []
        for i in range(4):
            temp = self.mo_mock_model(model_name=f"TempModel{i}Model")
            fk_models.append((temp._meta.app_label, temp.__name__))
        model = self.mo_mock_model(foreign_keys=fk_models)
        fk_fields = [
            f for f in model._meta.concrete_fields if isinstance(f, models.ForeignKey)
        ]
        self.asserts.assertEqual(len(fk_fields), 4)

    def test_app_name_empty_autofills_current_app(self):
        """BOUNDARY: Validates app name empty autofills current app."""
        model = self.mo_mock_model(app_name="")
        self._common_assertions(model)


@pytest.mark.django_db(transaction=True)
class TestMockModelFrms(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def _shared_app(self, _class_app_frms):
        self._app_name = _class_app_frms

    def _create_models(self, models_info, app_name=None):
        app = app_name or self._app_name
        created_list = []
        created_dict = {}
        for node in models_info:
            fk_resolved = [
                (app, created_dict[fk_name].__name__) for fk_name, in node["fk"]
            ]
            model_cls = self.mo_mock_model(
                model_name=node["name"],
                app_name=app,
                fields=node["fields"],
                foreign_keys=fk_resolved,
            )
            created_dict[node["name"]] = model_cls
            created_list.append(model_cls)
        return created_list

    @pytest.mark.parametrize(
        "model_info, counts, expected_df_counts",
        [
            # 1. Single model, no FK
            (
                [{"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}],
                [2],
                [2],
            ),
            # 2. Parent → Child
            (
                [
                    {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {
                        "name": "ChildModel",
                        "fields": copy.deepcopy(FIELDS),
                        "fk": [("ParentModel",)],
                    },
                ],
                [2, 1],
                [2, 2],
            ),
            # 3. Parent → Child → Grandchild
            (
                [
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
                ],
                [2, 1, 1],
                [2, 2, 2],
            ),
            # 4. Two root models, no FK between them
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                ],
                [2, 2],
                [2, 2],
            ),
            # 5. Two roots, one child off root 1
            (
                [
                    {"name": "Parent1Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {"name": "Parent2Model", "fields": copy.deepcopy(FIELDS), "fk": []},
                    {
                        "name": "Child11Model",
                        "fields": copy.deepcopy(FIELDS),
                        "fk": [("Parent1Model",)],
                    },
                ],
                [2, 2, 1],
                [2, 2, 2],
            ),
        ],
    )
    def test_mock_model_frms_structural(self, model_info, counts, expected_df_counts):
        """ACCEPTANCE: Validates mock model frms structural."""
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(models=models_list, counts=counts)
        for idx, (model, df) in enumerate(df_dict.items()):
            _validate_columns(model, df, exclude_columns=None)
            _validate_rows(df, idx, expected_df_counts[idx], modify_rows=[])
            _validate_foreign_keys(df_dict)

    def test_mock_model_frms_col_removed(self):
        """ACCEPTANCE: Validates mock model frms col removed."""
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
        """ACCEPTANCE: Validates mock model frms col modified."""
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
        """ACCEPTANCE: Validates mock model frms col modified removed."""
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

    def test_is_fk_as_id_false_returns_model_instances(self):
        """ACCEPTANCE: Validates is fk as id false returns model instances."""
        model_info = [
            {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []},
            {
                "name": "ChildModel",
                "fields": copy.deepcopy(FIELDS),
                "fk": [("ParentModel",)],
            },
        ]
        models_list = self._create_models(model_info)
        _, child_model = models_list
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=[1, 1], is_fk_as_id=False
        )
        child_df = df_dict[child_model]
        fk_col = next(
            f.db_column or f.get_attname_column()[1]
            for f in child_model._meta.concrete_fields
            if isinstance(f, ForeignKey)
        )
        fk_val = child_df[0, fk_col]
        assert not isinstance(
            fk_val, str
        ), f"Expected model instance in FK column, got {type(fk_val)}"

    def test_is_enforce_db_column_false_uses_field_names(self):
        """ACCEPTANCE: Validates is enforce db column false uses field names."""
        fields = {
            "my_field": models.CharField(max_length=50, db_column="db_col_name"),
        }
        model_info = [{"name": "ParentModel", "fields": fields, "fk": []}]
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=[1], is_enforce_db_column=False
        )
        df = df_dict[models_list[0]]
        assert "my_field" in df.columns
        assert "db_col_name" not in df.columns

    def test_is_uuid_hex_false_uses_uuid_objects(self):
        """ACCEPTANCE: Validates is uuid hex false uses uuid objects."""
        import uuid as uuid_mod

        model_info = [
            {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
        ]
        models_list = self._create_models(model_info)
        df_dict = self.mo_mock_model_frms(
            models=models_list, counts=[1], is_uuid_hex=False
        )
        df = df_dict[models_list[0]]
        pk_col = models_list[0]._meta.pk.db_column or models_list[0]._meta.pk.attname
        pk_val = df[0, pk_col]
        assert isinstance(
            pk_val, uuid_mod.UUID
        ), f"Expected UUID object when is_uuid_hex=False, got {type(pk_val)}"

    def test_omitting_counts_defaults_to_one_row_per_model(self):
        """BOUNDARY: Validates omitting counts defaults to one row per model."""
        models_list = self._create_models(
            [{"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}]
        )
        df_dict = self.mo_mock_model_frms(models=models_list)
        assert df_dict[models_list[0]].height == 1

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
        """REJECTION: Validates mock model frms rejections."""
        models_list = self._create_models(
            [{"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}]
        )
        with pytest.raises(expected_error):
            self.mo_mock_model_frms(
                models=models_list, exclude_columns=exclude_columns, modify=modify_rows
            )


@pytest.mark.django_db(transaction=True)
class TestUpdateMockModelFrms(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def _test_app(self, _class_app_update):
        self._test_app_name = _class_app_update

    def _base_df_dict(self, model_info=None, counts=None):
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

        app_name = self._test_app_name
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

    def _counts_from_df_dict(self, df_dict):
        return [df.height for df in df_dict.values()]

    def test_pk_columns_are_preserved(self):
        """ACCEPTANCE: Validates pk columns are preserved."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        original_pks = {
            model: df[model._meta.pk.db_column or model._meta.pk.attname].to_list()
            for model, df in df_dict.items()
        }
        updated = self.mo_update_mock_model_frms(
            df_dict, counts=self._counts_from_df_dict(df_dict)
        )
        for model, df in updated.items():
            pk_col = model._meta.pk.db_column or model._meta.pk.attname
            assert df[pk_col].to_list() == original_pks[model]

    def test_keep_columns_are_preserved(self):
        """ACCEPTANCE: Validates keep columns are preserved."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        models_list = list(df_dict.keys())
        original_name_vals = df_dict[models_list[0]]["name"].to_list()
        updated = self.mo_update_mock_model_frms(
            df_dict,
            counts=self._counts_from_df_dict(df_dict),
            keep_columns=[["name"]],
        )
        assert updated[models_list[0]]["name"].to_list() == original_name_vals

    def test_modify_applies_to_updated_df(self):
        """ACCEPTANCE: Validates modify applies to updated df."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        updated = self.mo_update_mock_model_frms(
            df_dict,
            counts=self._counts_from_df_dict(df_dict),
            modify=[{0: {"description": "overridden"}}],
        )
        model = list(updated.keys())[0]
        assert updated[model][0, "description"] == "overridden"

    def test_row_count_unchanged_after_update(self):
        """ACCEPTANCE: Validates row count unchanged after update."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[3],
        )
        updated = self.mo_update_mock_model_frms(
            df_dict, counts=self._counts_from_df_dict(df_dict)
        )
        model = list(updated.keys())[0]
        assert updated[model].height == df_dict[model].height

    def test_is_uuid_hex_false_in_update(self):
        """ACCEPTANCE: Validates is uuid hex false in update."""
        import uuid as uuid_mod

        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[1],
        )
        updated = self.mo_update_mock_model_frms(
            df_dict,
            counts=self._counts_from_df_dict(df_dict),
            is_uuid_hex=False,
        )
        model = list(updated.keys())[0]
        _ = model._meta.pk.db_column or model._meta.pk.attname
        assert updated[model].height == 1

    def test_omitting_counts_in_update_defaults_to_one_per_model(self):
        """BOUNDARY: Validates omitting counts in update defaults to one per model."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[1],
        )
        updated = self.mo_update_mock_model_frms(df_dict)
        for model, df in updated.items():
            assert df.height == df_dict[model].height

    def test_exclude_nonexistent_column_raises(self):
        """REJECTION: Validates exclude nonexistent column raises."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises(ValueError):
            self.mo_update_mock_model_frms(
                df_dict,
                counts=self._counts_from_df_dict(df_dict),
                exclude_columns=[["no_such_col"]],
            )

    def test_modify_nonexistent_column_raises(self):
        """REJECTION: Validates modify nonexistent column raises."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises(ValueError):
            self.mo_update_mock_model_frms(
                df_dict,
                counts=self._counts_from_df_dict(df_dict),
                modify=[{0: {"no_such_col": "bad"}}],
            )

    def test_modify_out_of_range_row_raises(self):
        """REJECTION: Validates modify out of range row raises."""
        df_dict = self._base_df_dict(
            model_info=[
                {"name": "ParentModel", "fields": copy.deepcopy(FIELDS), "fk": []}
            ],
            counts=[2],
        )
        with pytest.raises((IndexError, ValueError)):
            self.mo_update_mock_model_frms(
                df_dict,
                counts=self._counts_from_df_dict(df_dict),
                modify=[{99: {"name": "bad"}}],
            )


class TestValidateOrGenerateModelName:

    def test_generates_test1model_when_testmodel_taken(self):
        """ACCEPTANCE: Validates generates test1model when testmodel taken."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_model_name,
        )

        class M:
            pass

        M.__name__ = "TestModel"

        assert _validate_or_generate_model_name([M]) == "Test1Model"

    def test_increments_counter_when_multiple_taken(self):
        """ACCEPTANCE: Validates increments counter when multiple taken."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_model_name,
        )

        taken = []
        for name in ("TestModel", "Test1Model", "Test2Model"):

            class M:
                pass

            M.__name__ = name
            taken.append(M)

        assert _validate_or_generate_model_name(taken) == "Test3Model"

    def test_explicit_name_accepted_when_not_duplicate(self):
        """REJECTION: Validates explicit name accepted when not duplicate."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_model_name,
        )

        assert _validate_or_generate_model_name([], model_name="FooModel") == "FooModel"

    def test_explicit_duplicate_raises(self):
        """REJECTION: Validates explicit duplicate raises."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_model_name,
        )

        class M:
            pass

        M.__name__ = "FooModel"

        with pytest.raises(ValueError, match="already exists"):
            _validate_or_generate_model_name([M], model_name="FooModel")


class TestNormalizeFkParams:

    def test_two_element_fk_appends_required(self):
        """ACCEPTANCE: Validates two element fk appends required."""
        from apps.django_mindoff.components.tdd_kit import (
            _normalize_fk_and_validate_mockmodel_params,
        )

        _, result = _normalize_fk_and_validate_mockmodel_params(
            None, None, [("myapp", "MyModel")]
        )
        assert result[0] == ("myapp", "MyModel", "required")

    def test_optional_accepted(self):
        """ACCEPTANCE: Validates optional accepted."""
        from apps.django_mindoff.components.tdd_kit import (
            _normalize_fk_and_validate_mockmodel_params,
        )

        _, result = _normalize_fk_and_validate_mockmodel_params(
            None, None, [("myapp", "MyModel", "optional")]
        )
        assert result[0][2] == "optional"

    def test_invalid_option_raises(self):
        """REJECTION: Validates invalid option raises."""
        from apps.django_mindoff.components.tdd_kit import (
            _normalize_fk_and_validate_mockmodel_params,
        )

        with pytest.raises(Exception):
            _normalize_fk_and_validate_mockmodel_params(
                None, None, [("myapp", "MyModel", "cascade")]
            )


class TestApplyModify:

    def test_empty_modify_list_returns_df_unchanged(self):
        """BOUNDARY: Validates empty modify list returns df unchanged."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_modify

        df = pl.DataFrame({"name": ["a", "b"]})
        assert _apply_modify(0, df, []).equals(df)

    def test_idx_beyond_list_returns_df_unchanged(self):
        """ACCEPTANCE: Validates idx beyond list returns df unchanged."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_modify

        df = pl.DataFrame({"name": ["a"]})
        assert _apply_modify(5, df, [{0: {"name": "x"}}]).equals(df)

    def test_out_of_range_row_raises_index_error(self):
        """REJECTION: Validates out of range row raises index error."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_modify

        df = pl.DataFrame({"name": ["a"]})
        with pytest.raises(IndexError):
            _apply_modify(0, df, [{99: {"name": "x"}}])

    def test_nonexistent_column_raises_value_error(self):
        """REJECTION: Validates nonexistent column raises value error."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_modify

        df = pl.DataFrame({"name": ["a"]})
        with pytest.raises(ValueError, match="non-existing column"):
            _apply_modify(0, df, [{0: {"no_col": "x"}}])


class TestApplyExcludeColumns:

    def test_empty_cols_returns_df_unchanged(self):
        """BOUNDARY: Validates empty cols returns df unchanged."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_exclude_columns

        df = pl.DataFrame({"a": [1], "b": [2]})
        assert _apply_exclude_columns(0, df, []).equals(df)

    def test_nonexistent_col_raises_value_error(self):
        """REJECTION: Validates nonexistent col raises value error."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_exclude_columns

        df = pl.DataFrame({"a": [1]})
        with pytest.raises(ValueError, match="non-existing"):
            _apply_exclude_columns(0, df, [["zzz"]])

    def test_idx_beyond_list_returns_df_unchanged(self):
        """ACCEPTANCE: Validates idx beyond list returns df unchanged."""
        import polars as pl
        from apps.django_mindoff.components.tdd_kit import _apply_exclude_columns

        df = pl.DataFrame({"a": [1]})
        assert _apply_exclude_columns(99, df, [["a"]]).equals(df)


class TestFieldValueGenerator:

    class _FakeField:
        def __init__(
            self,
            *,
            name="field_1",
            internal_type="CharField",
            default=None,
            has_default=False,
            unique=False,
            max_length=20,
            validators=None,
            null=False,
            blank=False,
            unique_for_date=None,
            unique_for_month=None,
            unique_for_year=None,
            related_model=None,
        ):
            self.name = name
            self._internal_type = internal_type
            self._default = default
            self._has_default = has_default
            self.unique = unique
            self.max_length = max_length
            self.validators = validators or []
            self.null = null
            self.blank = blank
            self.unique_for_date = unique_for_date
            self.unique_for_month = unique_for_month
            self.unique_for_year = unique_for_year
            self.related_model = related_model

        def has_default(self):
            return self._has_default

        def get_default(self):
            return self._default

        def get_internal_type(self):
            return self._internal_type

    def test_generate_field_value_uses_default_and_uuid_hex_conversion(self):
        """BOUNDARY: Validates generate field value uses default and uuid hex conversion."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            generate_field_value,
        )

        default_uuid = uuid.uuid4()
        field = self._FakeField(
            internal_type="UUIDField",
            default=default_uuid,
            has_default=True,
        )
        out = generate_field_value(
            field, used_uniques={}, partial_kwargs={}, is_uuid_hex=True
        )
        assert out == default_uuid.hex

    def test_generate_field_value_uses_default_without_uuid_hex_conversion(self):
        """BOUNDARY: Validates generate field value uses default without uuid hex conversion."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            generate_field_value,
        )

        default_uuid = uuid.uuid4()
        field = self._FakeField(
            internal_type="UUIDField",
            default=default_uuid,
            has_default=True,
        )
        out = generate_field_value(
            field, used_uniques={}, partial_kwargs={}, is_uuid_hex=False
        )
        assert out == default_uuid

    def test_choose_null_blank_outcome_covers_all_cases(self, monkeypatch):
        """ACCEPTANCE: Validates choose null blank outcome covers all cases."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )

        both = self._FakeField(null=True, blank=True)
        g = FieldValueGenerator(both, {}, {}, True)
        monkeypatch.setattr("random.random", lambda: 0.005)
        assert g._choose_null_blank_outcome() == "null"
        monkeypatch.setattr("random.random", lambda: 0.015)
        assert g._choose_null_blank_outcome() == "blank"
        monkeypatch.setattr("random.random", lambda: 0.5)
        assert g._choose_null_blank_outcome() == "value"

        only_null = FieldValueGenerator(self._FakeField(null=True), {}, {}, True)
        monkeypatch.setattr("random.random", lambda: 0.5)
        assert only_null._choose_null_blank_outcome() == "null"

        only_blank = FieldValueGenerator(self._FakeField(blank=True), {}, {}, True)
        monkeypatch.setattr("random.random", lambda: 0.1)
        assert only_blank._choose_null_blank_outcome() == "blank"

    def test_gen_text_honors_unique_and_unique_for_date(self, monkeypatch):
        """ACCEPTANCE: Validates gen text honors unique and unique for date."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )

        field = self._FakeField(
            name="title",
            internal_type="CharField",
            unique=True,
            unique_for_date="created_at",
            max_length=10,
        )
        used_uniques = {
            "title": {"taken"},
            "title:date": {("repeat", datetime.date(2026, 1, 1))},
        }
        g = FieldValueGenerator(
            field=field,
            used_uniques=used_uniques,
            partial_kwargs={"created_at": datetime.datetime(2026, 1, 1, 10, 0, 0)},
            is_uuid_hex=True,
        )

        values = iter(["taken", "repeat", "goodone"])
        monkeypatch.setattr(
            "random.choices", lambda *args, **kwargs: list(next(values))
        )
        out = g._gen_text()
        assert out == "goodone"

    def test_check_unique_for_month_and_year(self):
        """ACCEPTANCE: Validates check unique for month and year."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )

        field = self._FakeField(
            name="code",
            internal_type="CharField",
            unique_for_month="created_at",
            unique_for_year="created_at",
        )
        used = {}
        g = FieldValueGenerator(
            field=field,
            used_uniques=used,
            partial_kwargs={"created_at": datetime.datetime(2025, 7, 4, 9, 30, 0)},
            is_uuid_hex=True,
        )
        assert g._check_unique_for("value-1") is True
        assert g._check_unique_for("value-1") is False

    def test_gen_int_respects_min_max_validators_and_unique(self, monkeypatch):
        """BOUNDARY: Validates gen int respects min max validators and unique."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )
        from django.core.validators import MinValueValidator, MaxValueValidator

        field = self._FakeField(
            name="age",
            internal_type="IntegerField",
            unique=True,
            validators=[MinValueValidator(10), MaxValueValidator(12)],
        )
        used = {"age": {11}}
        g = FieldValueGenerator(
            field=field, used_uniques=used, partial_kwargs={}, is_uuid_hex=True
        )
        choices = iter([11, 12])
        monkeypatch.setattr("random.randint", lambda a, b: next(choices))
        assert g._gen_int() == 12

    def test_gen_decimal_quantizes_to_decimal_places(self):
        """ACCEPTANCE: Validates gen decimal quantizes to decimal places."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )

        field = self._FakeField(internal_type="DecimalField")
        field.max_digits = 6
        field.decimal_places = 3
        out = FieldValueGenerator(field, {}, {}, True)._gen_decimalfield()
        assert isinstance(out, Decimal)
        assert abs(out.as_tuple().exponent) == 3

    def test_uuid_slug_float_bool_date_datetime_and_fallback(self, monkeypatch):
        """ACCEPTANCE: Validates uuid slug float bool date datetime and fallback."""
        from apps.django_mindoff.components._tdd_kit.field_value_generator import (
            FieldValueGenerator,
        )
        from apps.django_mindoff.components._tdd_kit import (
            field_value_generator as fvg_mod,
        )

        uuid_hex = FieldValueGenerator(
            self._FakeField(internal_type="UUIDField", unique=True, name="uid"),
            {"uid": set()},
            {},
            True,
        )._gen_uuidfield()
        assert isinstance(uuid_hex, str) and len(uuid_hex) == 32

        uuid_obj = FieldValueGenerator(
            self._FakeField(internal_type="UUIDField", unique=False),
            {},
            {},
            False,
        )._gen_uuidfield()
        assert isinstance(uuid_obj, uuid.UUID)

        slug = FieldValueGenerator(
            self._FakeField(internal_type="SlugField"), {}, {}, True
        )._gen_slugfield()
        assert isinstance(slug, str) and len(slug) == 8

        assert isinstance(
            FieldValueGenerator(
                self._FakeField(internal_type="FloatField"), {}, {}, True
            )._gen_floatfield(),
            float,
        )
        assert isinstance(
            FieldValueGenerator(
                self._FakeField(internal_type="BooleanField"), {}, {}, True
            )._gen_booleanfield(),
            bool,
        )
        assert isinstance(
            FieldValueGenerator(
                self._FakeField(internal_type="DateField"), {}, {}, True
            )._gen_datefield(),
            datetime.date,
        )
        assert isinstance(
            FieldValueGenerator(
                self._FakeField(internal_type="DateTimeField"), {}, {}, True
            )._gen_datetimefield(),
            datetime.datetime,
        )

        class Related:
            pass

        sentinel = object()
        monkeypatch.setattr(fvg_mod.baker, "prepare", lambda model: sentinel)
        fallback = FieldValueGenerator(
            self._FakeField(internal_type="UnknownType", related_model=Related),
            {},
            {},
            True,
        ).run()
        assert fallback is sentinel

        none_fallback = FieldValueGenerator(
            self._FakeField(internal_type="UnknownType", related_model=None),
            {},
            {},
            True,
        ).run()
        assert none_fallback is None


class TestValidateOrGenerateAppName:

    def test_auto_generated_name_has_expected_prefix(self):
        """ACCEPTANCE: Validates auto generated name has expected prefix."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_app_name,
        )

        generated = _validate_or_generate_app_name(created_apps=[], app_name=None)
        assert generated.startswith("app_")
        assert len(generated) > 4

    def test_duplicate_name_raises_when_is_exists_false(self):
        """REJECTION: Validates duplicate name raises when is exists false."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_app_name,
        )

        with pytest.raises(ValueError, match="already exists"):
            _validate_or_generate_app_name(created_apps=["my_app"], app_name="my_app")

    def test_missing_name_raises_when_is_exists_true(self):
        """REJECTION: Validates missing name raises when is exists true."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_app_name,
        )

        with pytest.raises(ValueError, match="does not exist"):
            _validate_or_generate_app_name(
                created_apps=["existing_app"],
                app_name="unknown_app",
                is_exists=True,
            )

    def test_existing_name_is_returned_when_is_exists_true(self):
        """ACCEPTANCE: Validates existing name is returned when is exists true."""
        from apps.django_mindoff.components.tdd_kit import (
            _validate_or_generate_app_name,
        )

        assert (
            _validate_or_generate_app_name(
                created_apps=["existing_app"],
                app_name="existing_app",
                is_exists=True,
            )
            == "existing_app"
        )


class TestUrlResolverHelpers:

    def test_is_versioned_url_true_with_wrapped_callback(self, monkeypatch):
        """ANOMALY: Validates is versioned url true with wrapped callback."""
        from types import SimpleNamespace
        from django.urls import path
        from apps.django_mindoff.components import tdd_kit

        def actual_cb(request):
            return None

        actual_cb.VERSION_MAP = {1: object}

        def wrapped_cb(request):
            return actual_cb(request)

        wrapped_cb.__wrapped__ = actual_cb
        patterns = [path("v/", wrapped_cb, name="versioned_api")]

        monkeypatch.setattr(
            tdd_kit, "get_resolver", lambda: SimpleNamespace(url_patterns=patterns)
        )

        assert tdd_kit._is_versioned_url("versioned_api") is True

    def test_is_versioned_url_false_when_name_not_found(self, monkeypatch):
        """REJECTION: Validates is versioned url false when name not found."""
        from types import SimpleNamespace
        from apps.django_mindoff.components import tdd_kit

        monkeypatch.setattr(
            tdd_kit, "get_resolver", lambda: SimpleNamespace(url_patterns=[])
        )
        assert tdd_kit._is_versioned_url("missing_api") is False

    def test_get_api_cls_attributes_raises_lookup_error(self, monkeypatch):
        """REJECTION: Validates get api cls attributes raises lookup error."""
        from types import SimpleNamespace
        from apps.django_mindoff.components import tdd_kit

        monkeypatch.setattr(
            tdd_kit, "get_resolver", lambda: SimpleNamespace(url_patterns=[])
        )
        with pytest.raises(LookupError, match="No URL found"):
            tdd_kit._get_api_cls_attributes("unknown_api")

    def test_get_api_cls_attributes_returns_view_class(self, monkeypatch):
        """ACCEPTANCE: Validates get api cls attributes returns view class."""
        from types import SimpleNamespace
        from django.urls import path
        from apps.django_mindoff.components import tdd_kit

        class FakeView:
            pass

        def cb(request):
            return None

        cb.view_class = FakeView
        patterns = [path("cbv/", cb, name="cbv_api")]
        monkeypatch.setattr(
            tdd_kit, "get_resolver", lambda: SimpleNamespace(url_patterns=patterns)
        )

        assert tdd_kit._get_api_cls_attributes("cbv_api") is FakeView

    def test_get_api_cls_attributes_uses_version_map_entry(self, monkeypatch):
        """ACCEPTANCE: Validates get api cls attributes uses version map entry."""
        from types import SimpleNamespace
        from django.urls import path
        from apps.django_mindoff.components import tdd_kit

        class V1View:
            pass

        def cb(request):
            return None

        cb.VERSION_MAP = {1: V1View}
        patterns = [path("router/", cb, name="router_api")]
        monkeypatch.setattr(
            tdd_kit, "get_resolver", lambda: SimpleNamespace(url_patterns=patterns)
        )

        assert tdd_kit._get_api_cls_attributes("router_api", version=1) is V1View

    def test_is_versioned_url_traverses_nested_urlresolver(self, monkeypatch):
        """ANOMALY: Validates is versioned url traverses nested urlresolver."""
        from types import SimpleNamespace
        from django.urls import path, include
        from apps.django_mindoff.components import tdd_kit

        def cb(request):
            return None

        cb.VERSION_MAP = {1: object}
        inner_patterns = [path("v/", cb, name="nested_versioned_api")]
        outer_patterns = [path("api/", include((inner_patterns, "nested")))]

        monkeypatch.setattr(
            tdd_kit,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=outer_patterns),
        )
        assert tdd_kit._is_versioned_url("nested_versioned_api") is True

    def test_get_api_cls_attributes_traverses_nested_urlresolver(self, monkeypatch):
        """ANOMALY: Validates get api cls attributes traverses nested urlresolver."""
        from types import SimpleNamespace
        from django.urls import path, include
        from apps.django_mindoff.components import tdd_kit

        class NestedView:
            pass

        def cb(request):
            return None

        cb.view_class = NestedView
        inner_patterns = [path("cbv/", cb, name="nested_cbv_api")]
        outer_patterns = [path("api/", include((inner_patterns, "nested")))]

        monkeypatch.setattr(
            tdd_kit,
            "get_resolver",
            lambda: SimpleNamespace(url_patterns=outer_patterns),
        )
        assert tdd_kit._get_api_cls_attributes("nested_cbv_api") is NestedView

    def test_resolve_callback_with_empty_version_map_raises(self):
        """REJECTION: Validates resolve callback with empty version map raises."""
        from apps.django_mindoff.components import tdd_kit

        def cb(request):
            return None

        cb.VERSION_MAP = {}
        with pytest.raises(Exception, match="empty VERSION_MAP"):
            getattr(tdd_kit, "__resolve_api_cls_from_callback")(
                cb, "empty_router", version=1
            )

    def test_resolve_callback_without_view_or_version_map_raises(self):
        """REJECTION: Validates resolve callback without view or version map raises."""
        from apps.django_mindoff.components import tdd_kit

        def cb(request):
            return None

        with pytest.raises(
            Exception, match="neither a 'view_class' nor a 'VERSION_MAP'"
        ):
            getattr(tdd_kit, "__resolve_api_cls_from_callback")(
                cb, "plain_function", version=None
            )

    def test_resolve_callback_raises_key_error_for_unregistered_version(self):
        """REJECTION: Validates resolve callback raises key error for unregistered version."""
        from apps.django_mindoff.components import tdd_kit

        class V1View:
            pass

        def cb(request):
            return None

        cb.VERSION_MAP = {1: V1View}
        with pytest.raises(KeyError, match="Version 2 is not registered"):
            getattr(tdd_kit, "__resolve_api_cls_from_callback")(
                cb, "router_api", version=2
            )


class TestValidateModel:

    def test_query_failure_path_raises_assertion_error(self):
        """REJECTION: Validates query failure path raises assertion error."""
        from types import SimpleNamespace
        from apps.django_mindoff.components.tdd_kit import _validate_model

        class _Field:
            name = "id"
            is_relation = False
            many_to_one = False

        class _Manager:
            @staticmethod
            def all():
                raise RuntimeError("query boom")

        class _Model:
            _meta = SimpleNamespace(
                db_table="tbl_fake",
                pk=SimpleNamespace(name="id"),
                concrete_fields=[_Field()],
            )
            objects = _Manager()

        with pytest.raises(AssertionError, match="Querying model failed"):
            _validate_model(_Model)


def _register_isolated_app():
    app_name = f"app_{uuid.uuid4().hex[:12]}"
    temp_dir = Path(tempfile.mkdtemp()).resolve()
    sys.path.insert(0, str(temp_dir))

    dotted_path = app_name
    creator = DjangoAppCreator(dotted_path, isolated=True)
    creator.project_root = temp_dir
    creator.app_dir = str(temp_dir / app_name)
    creator.settings_path = temp_dir / "dummy_settings.py"
    creator.urls_path = temp_dir / "dummy_urls.py"
    creator.run()

    mock_root_urlconf_name = f"urls_{app_name}"
    mock_root_path = temp_dir / f"{mock_root_urlconf_name}.py"
    mock_root_content = f"""
from django.urls import path, include
from {settings.ROOT_URLCONF} import urlpatterns as original_patterns
import {dotted_path}.urls

urlpatterns = original_patterns + [
    path(\'{app_name}/\', include(\'{dotted_path}.urls\')),
]
"""
    mock_root_path.write_text(mock_root_content)

    ov = override_settings(
        INSTALLED_APPS=list(settings.INSTALLED_APPS) + [dotted_path],
        ROOT_URLCONF=mock_root_urlconf_name,
    )
    ov.enable()
    apps.set_installed_apps(settings.INSTALLED_APPS)
    apps.clear_cache()
    clear_url_caches()
    return app_name, temp_dir, ov


def _unregister_isolated_app(app_name, temp_dir, ov):
    ov.disable()
    clear_url_caches()
    root_url_mod = f"urls_{app_name}"
    mods_to_remove = [
        m
        for m in sys.modules
        if m == app_name or m.startswith(f"{app_name}.") or m == root_url_mod
    ]
    for m in mods_to_remove:
        sys.modules.pop(m, None)
    sys.path[:] = [p for p in sys.path if str(p) != str(temp_dir)]
    shutil.rmtree(temp_dir, ignore_errors=True)
    apps.clear_cache()
    clear_url_caches()
    apps.populate(settings.INSTALLED_APPS)


@pytest.fixture(scope="class")
def _class_app_frms():
    app_name, temp_dir, ov = _register_isolated_app()
    yield app_name
    _unregister_isolated_app(app_name, temp_dir, ov)


@pytest.fixture(scope="class")
def _class_app_update():
    app_name, temp_dir, ov = _register_isolated_app()
    yield app_name
    _unregister_isolated_app(app_name, temp_dir, ov)


def _validate_columns(model, df, exclude_columns):
    for field in model._meta.concrete_fields:
        field_name = field.db_column or field.name
        if exclude_columns:
            for col in exclude_columns:
                assert col not in df.columns
            if field_name not in exclude_columns:
                assert field_name in df.columns
        else:
            assert field_name in df.columns


def _validate_rows(df, idx, expected_count, modify_rows):
    assert expected_count == df.height
    if len(modify_rows) > idx and modify_rows[idx]:
        for row_idx, modified_info in modify_rows[idx].items():
            for col, expected in modified_info.items():
                assert df[row_idx, col] == expected


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
                        assert current_fk_list == set(iter_fk_list)

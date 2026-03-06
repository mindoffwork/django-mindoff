from typing import Dict, Tuple, Type, Union

import polars as pl
from django.db import models


# ----------------
# Classes
# ----------------
class ColumnValidator:
    def __init__(
        self,
        model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        is_add_missing_columns: bool = True,
        is_remove_extra_columns: bool = True,
    ):
        self.model_frms = model_frms
        self.is_add_missing_columns = is_add_missing_columns
        self.is_remove_extra_columns = is_remove_extra_columns

    def _validate_pk_and_fk_db_columns(self, model_cls: Type[models.Model]):
        pk_field = model_cls._meta.pk

        # 1. Ensure primary key is UUIDField
        if not isinstance(pk_field, models.UUIDField):
            raise ValueError(
                f"Model {model_cls.__name__} must use UUIDField as primary key, "
                f"but found {type(pk_field).__name__}."
            )

        # 2. Ensure PK has db_column defined
        if not getattr(pk_field, "db_column", None):
            raise ValueError(
                f"Primary key field '{pk_field.name}' in model {model_cls.__name__} "
                f"must explicitly define db_column."
            )

        # 3. Ensure all ForeignKeys have db_column defined
        for field in model_cls._meta.concrete_fields:
            if isinstance(field, models.ForeignKey):
                if not getattr(field, "db_column", None):
                    raise ValueError(
                        f"ForeignKey field '{field.name}' in model {model_cls.__name__} "
                        f"must explicitly define db_column."
                    )

    def _get_model_field_mapping(self, model_cls: Type[models.Model]) -> Dict[str, str]:
        mapping = {}
        for field in model_cls._meta.concrete_fields:
            if getattr(field, "concrete", False) and not getattr(
                field, "auto_created", False
            ):
                db_column = getattr(field, "db_column", None)
                mapping[field.name] = db_column if db_column else field.name
        return mapping

    def _get_auto_add_fields(self, model_cls: Type[models.Model]) -> set:
        auto_add_fields = set()
        for field in model_cls._meta.concrete_fields:
            if isinstance(field, models.DateTimeField) and (
                getattr(field, "auto_now", False)
                or getattr(field, "auto_now_add", False)
            ):
                db_column = getattr(field, "db_column", None)
                auto_add_fields.add(db_column if db_column else field.name)
        return auto_add_fields

    def _get_columns(self, df: Union[pl.DataFrame, pl.LazyFrame]) -> list[str]:
        return list(
            (df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema).keys()
        )

    def _rename_to_field_names(
        self, df: Union[pl.DataFrame, pl.LazyFrame], reverse_map: Dict[str, str]
    ):
        to_rename = {
            col: reverse_map[col] for col in self._get_columns(df) if col in reverse_map
        }
        return df.rename(to_rename) if to_rename else df

    def _add_auto_fields(
        self,
        df: Union[pl.DataFrame, pl.LazyFrame],
        auto_add_columns: set,
        reverse_map: Dict[str, str],
    ):
        existing = set(self._get_columns(df))
        new_fields = []
        for col in auto_add_columns:
            field_name = reverse_map.get(col, col)
            if field_name not in existing:
                new_fields.append(pl.lit(None).alias(field_name))
        return df.with_columns(new_fields) if new_fields else df

    def _handle_missing_columns(
        self, df: Union[pl.DataFrame, pl.LazyFrame], missing: set
    ) -> Tuple[Union[pl.DataFrame, pl.LazyFrame], str]:
        if self.is_add_missing_columns:
            if missing:
                is_zero_rows = (
                    df.limit(1).collect().height == 0
                    if isinstance(df, pl.LazyFrame)
                    else df.height == 0
                )
                df = df.with_columns([pl.lit(None).alias(col) for col in missing])
                if is_zero_rows:
                    df = df.limit(0) if isinstance(df, pl.LazyFrame) else df.head(0)
        elif not self.is_add_missing_columns and missing:
            return df, f"Missing column(s): {', '.join(sorted(missing))}"
        return df, ""

    def _handle_extra_columns(
        self, df: Union[pl.DataFrame, pl.LazyFrame], extra: set
    ) -> Tuple[Union[pl.DataFrame, pl.LazyFrame], str]:
        if self.is_remove_extra_columns:
            keep_cols = list(set(self._get_columns(df)) - extra)
            return df.select(keep_cols), ""
        elif extra:
            return df, f"Unexpected column(s): {', '.join(sorted(extra))}"
        return df, ""

    def _rename_to_db_column_names(
        self, df: Union[pl.DataFrame, pl.LazyFrame], field_map: Dict[str, str]
    ):
        to_rename = {
            k: v for k, v in field_map.items() if k != v and k in self._get_columns(df)
        }
        return df.rename(to_rename) if to_rename else df

    def _with_error_column(self, df: Union[pl.DataFrame, pl.LazyFrame], error_msg: str):
        return df.with_columns(pl.lit(error_msg).alias("error"))

    def run(
        self,
    ) -> Tuple[
        Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
    ]:
        valid_dfs = {}
        invalid_dfs = {}

        for model_cls, df in self.model_frms.items():
            original_df = df
            try:
                self._validate_pk_and_fk_db_columns(model_cls)
                field_map = self._get_model_field_mapping(model_cls)
                reverse_map = {v: k for k, v in field_map.items() if v != k}

                df = self._rename_to_field_names(df, reverse_map)
                auto_add_columns = self._get_auto_add_fields(model_cls)
                df = self._add_auto_fields(df, auto_add_columns, reverse_map)

                expected_fields = set(field_map.keys())
                current_fields = set(self._get_columns(df))

                missing = expected_fields - current_fields
                extra = current_fields - expected_fields

                df, missing_error = self._handle_missing_columns(df, missing)
                df, extra_error = self._handle_extra_columns(df, extra)

                pk_field_name = model_cls._meta.pk.name
                pk_db_column = (
                    getattr(model_cls._meta.pk, "db_column", None) or pk_field_name
                )
                current_fields = set(self._get_columns(df))
                if (
                    pk_field_name not in current_fields
                    and pk_db_column not in current_fields
                ):
                    raise ValueError(
                        f"Missing required primary key column in {model_cls}: '{pk_field_name}' (db_column='{pk_db_column}')"
                    )

                df = self._rename_to_db_column_names(df, field_map)

                error_msgs = list(filter(None, [missing_error, extra_error]))
                if error_msgs:
                    invalid_dfs[model_cls] = self._with_error_column(
                        df, "; ".join(error_msgs)
                    )
                else:
                    valid_dfs[model_cls] = df
            except Exception as e:
                invalid_dfs[model_cls] = self._with_error_column(original_df, str(e))

        # Ensure all input models have an entry
        for model_cls in self.model_frms:
            valid_dfs.setdefault(
                model_cls,
                (
                    pl.LazyFrame([])
                    if isinstance(self.model_frms[model_cls], pl.LazyFrame)
                    else pl.DataFrame()
                ),
            )
            invalid_dfs.setdefault(
                model_cls,
                (
                    pl.LazyFrame([])
                    if isinstance(self.model_frms[model_cls], pl.LazyFrame)
                    else pl.DataFrame()
                ),
            )

        return valid_dfs, invalid_dfs

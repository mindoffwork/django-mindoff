import polars as pl
from django.db import models
from django.db.models.fields.related import ForeignKey, OneToOneField

from ..polars_kit import mo_polars_kit
from ..validation_kit import mo_validation_kit
from django.conf import settings

# ----------------
# Constants
# ----------------
ERROR_COL = getattr(settings, "POLARS_VALIDATOR_ERROR_COL", None) or "__error__info"


# ----------------
# Classes
# ----------------
class ForeignKeyValidator:
    """
    Validate foreign-key integrity across incoming model DataFrames.

    This validator ensures that every foreign-key value in each model frame points
    to an existing related primary key, either from another provided frame or from
    the database when the related model frame is not supplied.
    """
    def __init__(self, df_dict: dict[type[models.Model], pl.DataFrame | pl.LazyFrame]):
        self.df_dict = df_dict

    def validate(self) -> dict[type[models.Model], pl.DataFrame | pl.LazyFrame]:
        """
        Validate foreign keys for all model frames and return validated frames.
        """
        return {
            model: self._validate_model_foreign_keys(model, df)
            for model, df in self.df_dict.items()
        }

    def _validate_model_foreign_keys(self, model, df):
        if mo_polars_kit.is_frm_empty(df):
            return df
        for field in model._meta.concrete_fields:
            if ERROR_COL not in df.columns:
                df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(ERROR_COL))

            if not isinstance(field, (ForeignKey, OneToOneField)):
                continue

            db_col = field.db_column
            mo_validation_kit.ensure_in(
                db_col,
                df.columns,
                msg=f"Missing foreign key column '{db_col}' in DataFrame for model '{model.__name__}'",
                is_exception=True,
            )

            related_model = field.related_model
            related_pk_field = related_model._meta.pk
            related_pk_col = (
                related_pk_field.db_column or related_pk_field.attname or "id"
            )
            related_pk_name = related_pk_field.attname
            if related_model in self.df_dict:
                related_df = self.df_dict[related_model].select(
                    pl.col(related_pk_col).cast(pl.Utf8).alias(related_pk_col)
                )
                invalid_fk_df = (
                    df.select(pl.col(db_col).cast(pl.Utf8).alias(db_col))
                    .drop_nulls()
                    .join(
                        related_df, left_on=db_col, right_on=related_pk_col, how="anti"
                    )
                )
                is_invalid_fk = mo_polars_kit.get_frm_height(invalid_fk_df) > 0
            else:
                fk_values = self._get_distinct_fk_values(df, db_col)
                if not fk_values:
                    continue
                existing_count = related_model.objects.filter(
                    **{f"{related_pk_name}__in": fk_values}
                ).count()
                is_invalid_fk = existing_count < len(fk_values)
            mo_validation_kit.ensure_falsey(
                is_invalid_fk,
                msg=f"Model '{model.__name__}' couldn't resolve foreign key(s) in column '{db_col}'.",
                is_exception=True,
            )
        return df

    def _get_distinct_fk_values(
        self, df: pl.DataFrame | pl.LazyFrame, col: str
    ) -> list:
        try:
            df_fk = df.select(pl.col(col).drop_nulls().cast(pl.Utf8).unique())
            if isinstance(df, pl.LazyFrame):
                return df_fk.collect(engine="streaming").get_column(col).to_list()
            else:
                return df_fk.get_column(col).to_list()
        except Exception as e:
            raise ValueError(
                f"Error extracting unique FK values from column '{col}': {e}"
            )

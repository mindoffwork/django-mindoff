import time
import uuid
from typing import Dict, Type, Union
from urllib.parse import quote_plus

import polars as pl
from django.conf import settings
from django.db import connection, models
from sqlalchemy import MetaData, Table, create_engine, inspect, literal, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.schema import quoted_name
from typeguard import typechecked

from ..polars_kit import mo_polars_kit
from ..response_kit import mo_validation_kit


# ----------------
# Classes
# ----------------
@typechecked
class CRUDProcessor:
    def __init__(
        self,
        model_frame_map: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        db_alias: str = "default",
    ):
        self.model_frame_map = model_frame_map
        self.db_alias = db_alias
        self.dialect = None
        self.engine = self._get_sqlalchemy_engine()

    def _get_sqlalchemy_engine(self):
        if self.db_alias not in settings.DATABASES:
            raise ValueError(
                f"Database alias '{self.db_alias}' not found in settings.DATABASES"
            )

        db = settings.DATABASES[self.db_alias]
        engine = db["ENGINE"]
        user = quote_plus(db.get("USER", ""))
        password = quote_plus(db.get("PASSWORD", ""))
        host = db.get("HOST", "localhost")
        port = db.get("PORT", "")
        name = db["NAME"]

        if "mysql" in engine:
            self.dialect = "mysql+pymysql"
        elif "postgresql" in engine or "postgres" in engine:
            self.dialect = "postgresql+psycopg2"
        elif "sqlite" in engine:
            self.dialect = "sqlite"
            connection.ensure_connection()
            return create_engine("sqlite://", creator=lambda: connection.connection)
        else:
            raise ValueError(f"Unsupported database engine: {engine}")

        auth_part = f"{user}:{password}@" if user or password else ""
        port_part = f":{port}" if port else ""
        connection.ensure_connection()
        return create_engine(f"{self.dialect}://{auth_part}{host}{port_part}/{name}")

    @staticmethod
    def _track_time(func):
        def wrapper(self, *args, **kwargs):
            start = time.time()
            try:
                affected_tables = func(self, *args, **kwargs)
                return {
                    "message": "Database Operation successful",
                    "affected_tables": affected_tables,
                    "time_taken_seconds": round(time.time() - start, 4),
                }
            except Exception as e:
                raise RuntimeError(f"CRUD operation failed: {e}") from e

        return wrapper

    @_track_time
    def create(self, batch_size: int) -> list[str]:
        saved_tables = []
        with self.engine.begin() as conn:
            for model, df in self.model_frame_map.items():
                table = model._meta.db_table
                table_name = quoted_name(model._meta.db_table, quote=True)
                inspector = inspect(conn)
                mo_validation_kit.ensure_in(
                    table,
                    inspector.get_table_names(),
                    msg=f"Table '{table}' does not exist.",
                    is_exception=True,
                )
                if isinstance(df, pl.LazyFrame):
                    df.collect(engine="streaming").write_database(
                        table_name=table_name,
                        connection=conn,
                        if_table_exists="append",
                        engine="sqlalchemy",
                    )
                else:
                    df.write_database(
                        table_name=table_name,
                        connection=conn,
                        if_table_exists="append",
                        engine="sqlalchemy",
                    )
                saved_tables.append(table)
        return saved_tables

    @_track_time
    def update(self, is_temp_table: bool, batch_size: int) -> list[str]:
        affected_tables = []
        with self.engine.begin() as conn:
            metadata = MetaData()

            for model, df in self.model_frame_map.items():
                model_table = model._meta.db_table
                table_name = quoted_name(model_table, quote=True)

                inspector = inspect(conn)
                mo_validation_kit.ensure_in(
                    model_table,
                    inspector.get_table_names(),
                    msg=f"Table '{model_table}' does not exist.",
                    is_exception=True,
                )

                table = Table(table_name, metadata, autoload_with=conn)
                pk_field = model._meta.pk.column or model._meta.pk.name
                pk_col = quoted_name(pk_field, quote=True)
                temp_table_name = f"temp_upsert_{uuid.uuid4().hex}"

                self._update__df_insert(
                    df,
                    is_temp_table=is_temp_table,
                    temp_table_name=temp_table_name,
                    table=table,
                    pk_field=pk_field,
                    pk_col=pk_col,
                    conn=conn,
                )

                if is_temp_table:
                    self._update__merge_staging(
                        table, temp_table_name, metadata, pk_field, pk_col, conn
                    )

                affected_tables.append(model_table)

        return affected_tables

    def _update__df_insert(
        self,
        df: pl.DataFrame | pl.LazyFrame,
        *,
        is_temp_table: bool,
        temp_table_name: str,
        table,
        pk_field: str,
        pk_col: str,
        conn,
    ) -> None:
        if is_temp_table:
            if isinstance(df, pl.LazyFrame):
                df.collect(engine="streaming").write_database(
                    table_name=temp_table_name,
                    connection=conn,
                    if_table_exists="append",
                    engine="sqlalchemy",
                )
            else:
                df.write_database(
                    table_name=temp_table_name,
                    connection=conn,
                    if_table_exists="append",
                    engine="sqlalchemy",
                )
        else:
            if isinstance(df, pl.LazyFrame):
                df_rows = df.collect(engine="streaming").to_arrow().to_pylist()
            else:
                df_rows = df.to_arrow().to_pylist()
            if self.dialect == "postgresql":
                insert_stmt = table.insert().values(df_rows)
                update_cols = {
                    c.name: insert_stmt.excluded[c.name]
                    for c in table.columns
                    if c.name != pk_field
                }
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[pk_col], set_=update_cols
                )
                conn.execute(stmt)

            elif self.dialect == "mysql":
                insert_stmt = table.insert().values(df_rows)
                update_cols = {
                    c.name: insert_stmt.inserted[c.name]
                    for c in table.columns
                    if c.name != pk_field
                }
                stmt = insert_stmt.on_duplicate_key_update(update_cols)
                conn.execute(stmt)

            elif self.dialect == "sqlite":
                insert_stmt = sqlite_insert(table).values(df_rows)

                update_cols = {
                    c.name: insert_stmt.excluded[c.name]
                    for c in table.columns
                    if c.name != pk_field
                }

                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[pk_field],
                    set_=update_cols,
                )
                conn.execute(stmt)

    def _update__merge_staging(
        self, table, temp_table_name, metadata, pk_field, pk_col, conn
    ):
        temp_table = Table(temp_table_name, metadata, autoload_with=conn)
        temp_select = select(temp_table)
        mo_validation_kit.ensure_in(
            self.dialect,
            ["sqlite", "postgresql", "mysql"],
            msg="No Valid Dialect Found for Update Operation",
            is_exception=True,
        )
        if self.dialect == "sqlite":
            mo_validation_kit.ensure_in(
                pk_col,
                table.c,
                msg=f"Primary key {pk_col!r} not found in {table.name}",
                is_exception=True,
            )
            other_cols = [c.name for c in table.columns if c.name != pk_col]
            if not other_cols:
                return
            col_names = [pk_col] + other_cols
            ordered_temp = temp_select.with_only_columns(
                *(temp_select.c[name] for name in col_names)
            ).where(literal(True))
            insert_stmt = sqlite_insert(table).from_select(col_names, ordered_temp)
            update_cols = {c: insert_stmt.excluded[c] for c in other_cols}
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[table.c[pk_col]], set_=update_cols
            )
            conn.execute(stmt)

        elif self.dialect == "mysql":
            insert_stmt = mysql_insert(table).from_select(
                [c.name for c in table.columns], temp_select
            )
            update_cols = {
                c.name: insert_stmt.inserted[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_duplicate_key_update(update_cols)
            conn.execute(stmt)

        elif self.dialect == "postgresql":
            insert_stmt = pg_insert(table).from_select(
                [c.name for c in table.columns], temp_select
            )
            update_cols = {
                c.name: insert_stmt.excluded[c.name]
                for c in table.columns
                if c.name != pk_field
            }
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk_col], set_=update_cols
            )
            conn.execute(stmt)

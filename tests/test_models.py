"""models.py matches the real tables, column for column."""

import pytest
from sqlalchemy import inspect

from backend.models import Base

MODELS = sorted(Base.metadata.tables.values(), key=lambda t: t.name)


def test_all_19_tables_have_a_model(engine):
    db_tables = set(inspect(engine).get_table_names(schema="public"))
    assert db_tables == {t.name for t in MODELS}
    assert len(db_tables) == 19


@pytest.mark.parametrize("table", MODELS, ids=lambda t: t.name)
def test_columns_match(engine, table):
    db_cols = {c["name"]: c for c in inspect(engine).get_columns(table.name, schema="public")}
    assert set(db_cols) == set(table.columns.keys())
    for col in table.columns:
        db_col = db_cols[col.name]
        assert col.nullable == db_col["nullable"], f"{table.name}.{col.name} nullable differs"
        # generated columns must be read-only in the ORM
        assert (col.computed is not None) == ("computed" in db_col), f"{table.name}.{col.name} computed differs"


@pytest.mark.parametrize("table", MODELS, ids=lambda t: t.name)
def test_primary_key_matches(engine, table):
    pk = inspect(engine).get_pk_constraint(table.name, schema="public")["constrained_columns"]
    assert pk == [c.name for c in table.primary_key.columns]

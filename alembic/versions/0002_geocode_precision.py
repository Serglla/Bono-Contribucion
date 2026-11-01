"""geocode_cache.precision: qué tan confiable es cada punto del mapa.

Una base nueva ya la trae por create_all (se marca en 0001 y después pasa por acá),
por eso se fija antes de agregarla.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    columnas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("geocode_cache")}
    if "precision" not in columnas:
        op.add_column("geocode_cache", sa.Column("precision", sa.String(), nullable=True))


def downgrade():
    op.drop_column("geocode_cache", "precision")

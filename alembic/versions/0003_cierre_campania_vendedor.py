"""vendedores: cierre de campaña (fecha_fin_campania + cierre_snapshot).

fecha_fin_campania = cuándo se finalizó la campaña del vendedor (NULL = sigue
vendiendo). cierre_snapshot = JSON con las cifras del informe al momento del
cierre (las bajas siguen llegando después desde cobranza; el snapshot permite
comparar "% al cierre" contra "% hoy").

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    columnas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("vendedores")}
    if "fecha_fin_campania" not in columnas:
        op.add_column("vendedores", sa.Column("fecha_fin_campania", sa.DateTime(), nullable=True))
    if "cierre_snapshot" not in columnas:
        op.add_column("vendedores", sa.Column("cierre_snapshot", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("vendedores", "cierre_snapshot")
    op.drop_column("vendedores", "fecha_fin_campania")

"""Base: el esquema tal como estaba al 22/10/2026.

Hasta acá el esquema lo armaban `create_all` + los pasos de
app/migraciones/legacy.py en cada arranque. Una base anterior a Alembic se
lleva a este estado con esos pasos y se marca en esta revisión (lo hace
app/migraciones/__init__.py::asegurar_esquema).

Esta revisión no hace nada por sí sola: es el punto de partida. Los cambios
nuevos van en 0002, 0003... con `down_revision` apuntando a la anterior.
Ejemplo de una revisión nueva:

    revision = "0002"
    down_revision = "0001"

    def upgrade():
        op.add_column("boletas", sa.Column("observacion", sa.String(), nullable=True))

    def downgrade():
        op.drop_column("boletas", "observacion")

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

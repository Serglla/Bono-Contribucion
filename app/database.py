import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bonos.db")

# Railway a veces usa postgres:// en vez de postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Opciones del engine según el motor.
# - Postgres (Railway): pool_pre_ping verifica que la conexión esté viva antes de
#   usarla (Railway corta las conexiones inactivas; sin esto, la próxima request
#   reusa una conexión muerta, espera el timeout y recién reconecta → lentitud y
#   cuelgues intermitentes). pool_recycle las renueva antes de que el server las cierre.
# - SQLite (local): check_same_thread=False para poder usarla entre threads.
if DATABASE_URL.startswith("postgresql"):
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=1800,   # reciclar conexiones cada 30 min
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

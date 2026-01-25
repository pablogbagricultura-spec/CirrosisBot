import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")

PERSONS_SEED = ["Pablo", "Javi", "Jesus", "Fer", "Cuco", "Oli", "Emilio"]

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada en variables de entorno.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Crea tablas e inserta datos iniciales (personas + tipos de bebida si quieres)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Tablas
            cur.execute("""
            CREATE TABLE IF NOT EXISTS persons (
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('NEW','ACTIVE','INACTIVE')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS person_accounts (
              id SERIAL PRIMARY KEY,
              person_id INT NOT NULL REFERENCES persons(id),
              telegram_user_id BIGINT NOT NULL,
              assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              unassigned_at TIMESTAMPTZ,
              is_active BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_person_accounts_person_active
              ON person_accounts(person_id) WHERE is_active = TRUE;

            CREATE UNIQUE INDEX IF NOT EXISTS ux_person_accounts_tg_active
              ON person_accounts(telegram_user_id) WHERE is_active = TRUE;

            CREATE INDEX IF NOT EXISTS idx_persons_status ON persons(status);

            CREATE TABLE IF NOT EXISTS drink_types (
              id SERIAL PRIMARY KEY,
              code TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL,
              category TEXT NOT NULL CHECK(category IN ('BEER','OTHER')),
              volume_liters NUMERIC(6,3),
              unit_price_eur NUMERIC(10,2) NOT NULL CHECK(unit_price_eur >= 0),
              is_active BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS drink_events (
              id SERIAL PRIMARY KEY,
              person_id INT NOT NULL REFERENCES persons(id),
              telegram_user_id BIGINT NOT NULL,
              drink_type_id INT NOT NULL REFERENCES drink_types(id),
              quantity INT NOT NULL CHECK(quantity > 0),
              consumed_at DATE NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              year_start INT NOT NULL,
              volume_liters_total NUMERIC(10,3),
              price_eur_total NUMERIC(10,2) NOT NULL CHECK(price_eur_total >= 0),
              is_void BOOLEAN NOT NULL DEFAULT FALSE,
              voided_at TIMESTAMPTZ,
              voided_by_telegram_user_id BIGINT
            );

            CREATE INDEX IF NOT EXISTS idx_events_person_recent
              ON drink_events(person_id, is_void, created_at DESC);
            """)
            conn.commit()

        # Seed de personas (lista cerrada)
        with conn.cursor() as cur:
            for name in PERSONS_SEED:
                cur.execute(
                    "INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT (name) DO NOTHING;",
                    (name,)
                )
            conn.commit()

def get_assigned_person(telegram_user_id: int):
    """Devuelve la persona asignada a este usuario, o None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT p.id, p.name
              FROM person_accounts pa
              JOIN persons p ON p.id = pa.person_id
              WHERE pa.telegram_user_id = %s AND pa.is_active = TRUE
              LIMIT 1;
            """, (telegram_user_id,))
            return cur.fetchone()

def list_available_persons():
    """Personas libres (status NEW)"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT id, name
              FROM persons
              WHERE status = 'NEW'
              ORDER BY name;
            """)
            return cur.fetchall()

def assign_person(telegram_user_id: int, person_id: int):
    """
    Asigna una persona a un telegram_user_id.
    - Si el usuario ya tenía asignación -> devuelve ('ALREADY', person)
    - Si la persona ya no está libre -> ('TAKEN', None)
    - Si ok -> ('OK', person)
    """
    existing = get_assigned_person(telegram_user_id)
    if existing:
        return ("ALREADY", existing)

    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                # Bloquea la fila para evitar carreras
                cur.execute("SELECT id, name, status FROM persons WHERE id = %s FOR UPDATE;", (person_id,))
                row = cur.fetchone()
                if not row or row["status"] != "NEW":
                    conn.rollback()
                    return ("TAKEN", None)

                # Marca persona como activa
                cur.execute("UPDATE persons SET status = 'ACTIVE' WHERE id = %s;", (person_id,))

                # Crea asignación activa
                cur.execute("""
                  INSERT INTO person_accounts(person_id, telegram_user_id, is_active)
                  VALUES (%s, %s, TRUE);
                """, (person_id, telegram_user_id))

                conn.commit()
                return ("OK", {"id": row["id"], "name": row["name"]})
        except psycopg2.Error:
            conn.rollback()
            # Si hubo conflicto por índice único, lo tratamos como TAKEN/ALREADY
            existing2 = get_assigned_person(telegram_user_id)
            if existing2:
                return ("ALREADY", existing2)
            return ("TAKEN", None)

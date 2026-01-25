import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")

PERSONS_SEED = ["Pablo", "Javi", "Jesus", "Fer", "Cuco", "Oli", "Emilio"]

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:

            # --- PERSONAS ---
            cur.execute("""
            CREATE TABLE IF NOT EXISTS persons (
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK (status IN ('NEW','ACTIVE','INACTIVE')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)

            # --- ASIGNACIONES ---
            cur.execute("""
            CREATE TABLE IF NOT EXISTS person_accounts (
              id SERIAL PRIMARY KEY,
              person_id INT NOT NULL REFERENCES persons(id),
              telegram_user_id BIGINT NOT NULL,
              assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              unassigned_at TIMESTAMPTZ,
              is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
            """)

            cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_person_accounts_person_active
              ON person_accounts(person_id) WHERE is_active = TRUE;
            """)

            cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_person_accounts_tg_active
              ON person_accounts(telegram_user_id) WHERE is_active = TRUE;
            """)

            # --- TIPOS DE BEBIDA ---
            cur.execute("""
            CREATE TABLE IF NOT EXISTS drink_types (
              id SERIAL PRIMARY KEY,
              code TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL,
              category TEXT NOT NULL CHECK (category IN ('BEER','OTHER')),
              volume_liters NUMERIC(6,3),
              unit_price_eur NUMERIC(10,2) NOT NULL CHECK (unit_price_eur >= 0),
              is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
            """)

            # --- EVENTOS ---
            cur.execute("""
            CREATE TABLE IF NOT EXISTS drink_events (
              id SERIAL PRIMARY KEY,
              person_id INT NOT NULL REFERENCES persons(id),
              telegram_user_id BIGINT NOT NULL,
              drink_type_id INT NOT NULL REFERENCES drink_types(id),
              quantity INT NOT NULL CHECK (quantity > 0),
              consumed_at DATE NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              year_start INT
            );
            """)

            # --- MIGRACIONES SEGURAS ---
            cur.execute("""
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS is_void BOOLEAN NOT NULL DEFAULT FALSE;
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ;
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS voided_by_telegram_user_id BIGINT;
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS volume_liters_total NUMERIC(10,3);
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS price_eur_total NUMERIC(10,2);
            ALTER TABLE drink_events
              ADD COLUMN IF NOT EXISTS year_start INT;
            """)

            # --- ÍNDICES ---
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_person_recent
              ON drink_events(person_id, is_void, created_at DESC);
            """)

            conn.commit()

        # --- SEED DE PERSONAS ---
        with conn.cursor() as cur:
            for name in PERSONS_SEED:
                cur.execute(
                    "INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT (name) DO NOTHING;",
                    (name,)
                )
            conn.commit()

def get_assigned_person(telegram_user_id: int):
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
    existing = get_assigned_person(telegram_user_id)
    if existing:
        return ("ALREADY", existing)

    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, status FROM persons WHERE id = %s FOR UPDATE;",
                    (person_id,)
                )
                row = cur.fetchone()
                if not row or row["status"] != "NEW":
                    conn.rollback()
                    return ("TAKEN", None)

                cur.execute(
                    "UPDATE persons SET status = 'ACTIVE' WHERE id = %s;",
                    (person_id,)
                )

                cur.execute("""
                INSERT INTO person_accounts(person_id, telegram_user_id, is_active)
                VALUES (%s, %s, TRUE);
                """, (person_id, telegram_user_id))

                conn.commit()
                return ("OK", {"id": row["id"], "name": row["name"]})

        except psycopg2.Error:
            conn.rollback()
            existing2 = get_assigned_person(telegram_user_id)
            if existing2:
                return ("ALREADY", existing2)
            return ("TAKEN", None)

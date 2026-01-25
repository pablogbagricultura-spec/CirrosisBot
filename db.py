import os
import datetime as dt
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")

PERSONS_SEED = ["Pablo", "Javi", "Jesus", "Fer", "Cuco", "Oli", "Emilio"]

DRINKS_SEED = [
    ("CORTAITA","Cortaita","BEER",0.25,1.65),
    ("CANA","Caña","BEER",0.25,1.50),
    ("JARRITA","Jarrita","BEER",0.25,2.00),
    ("BOTELLIN","Botellín","BEER",0.20,1.25),
    ("TERCIO","Tercio","BEER",0.33,2.25),
    ("LATA33","Lata 33","BEER",0.33,0.60),
    ("JARRA","Jarra","BEER",0.40,3.00),
    ("TANQUE","Tanque","BEER",0.50,3.50),
    ("LATA50","Lata 50","BEER",0.50,1.00),
    ("LITRO","Litro","BEER",1.00,2.00),
    ("CUBATA","Cubata","OTHER",None,6.50),
    ("PIEDRA","Piedra","OTHER",None,6.00),
    ("CHUPITO","Chupito","OTHER",None,2.00),
]

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada en Railway (Variables).")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def beer_year_start_for(d: dt.date) -> int:
    # Año cervecero: 7 enero -> 6 enero
    jan7 = dt.date(d.year, 1, 7)
    return d.year if d >= jan7 else (d.year - 1)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # PERSONAS
            cur.execute("""
            CREATE TABLE IF NOT EXISTS persons (
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK (status IN ('NEW','ACTIVE','INACTIVE')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)

            # ASIGNACIÓN persona <-> telegram
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

            # TIPOS DE BEBIDA
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

            # EVENTOS (mínimo)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS drink_events (
              id SERIAL PRIMARY KEY,
              person_id INT NOT NULL REFERENCES persons(id),
              drink_type_id INT NOT NULL REFERENCES drink_types(id),
              quantity INT NOT NULL CHECK (quantity > 0),
              consumed_at DATE NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)

            # MIGRACIONES SUAVES (por si existía de antes)
            cur.execute("""
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;

            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS year_start INT;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS volume_liters_total NUMERIC(10,3);
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS price_eur_total NUMERIC(10,2);

            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS is_void BOOLEAN NOT NULL DEFAULT FALSE;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS voided_by_telegram_user_id BIGINT;
            """)

            # ÍNDICES
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_person_recent
              ON drink_events(person_id, is_void, created_at DESC);
            """)
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_year
              ON drink_events(year_start, is_void);
            """)

            # CONTROL DE ENVÍO DE RESUMEN MENSUAL (para no duplicar)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS monthly_summaries_sent (
              id SERIAL PRIMARY KEY,
              year INT NOT NULL,
              month INT NOT NULL,
              sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(year, month)
            );
            """)

            conn.commit()

        # Seed personas
        with conn.cursor() as cur:
            for name in PERSONS_SEED:
                cur.execute(
                    "INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT (name) DO NOTHING;",
                    (name,)
                )
            conn.commit()

        # Seed bebidas
        with conn.cursor() as cur:
            for code, label, cat, vol, price in DRINKS_SEED:
                cur.execute("""
                    INSERT INTO drink_types(code,label,category,volume_liters,unit_price_eur,is_active)
                    VALUES (%s,%s,%s,%s,%s,TRUE)
                    ON CONFLICT (code) DO NOTHING;
                """, (code, label, cat, vol, price))
            conn.commit()

# -------------------------
# Usuarios / asignaciones
# -------------------------

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
            WHERE status='NEW'
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
                cur.execute("SELECT id, name, status FROM persons WHERE id=%s FOR UPDATE;", (person_id,))
                row = cur.fetchone()
                if not row or row["status"] != "NEW":
                    conn.rollback()
                    return ("TAKEN", None)

                cur.execute("UPDATE persons SET status='ACTIVE' WHERE id=%s;", (person_id,))
                cur.execute("""
                    INSERT INTO person_accounts(person_id, telegram_user_id, is_active)
                    VALUES (%s,%s,TRUE);
                """, (person_id, telegram_user_id))
                conn.commit()
                return ("OK", {"id": row["id"], "name": row["name"]})
        except psycopg2.Error:
            conn.rollback()
            existing2 = get_assigned_person(telegram_user_id)
            if existing2:
                return ("ALREADY", existing2)
            return ("TAKEN", None)

def list_active_telegram_user_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT DISTINCT telegram_user_id
            FROM person_accounts
            WHERE is_active=TRUE;
            """)
            return [r["telegram_user_id"] for r in cur.fetchall()]

# -------------------------
# Bebidas / eventos
# -------------------------

def list_drink_types(category: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, label
            FROM drink_types
            WHERE is_active=TRUE AND category=%s
            ORDER BY label;
            """, (category,))
            return cur.fetchall()

def get_drink_type(drink_type_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, label, volume_liters, unit_price_eur
            FROM drink_types
            WHERE id=%s;
            """, (drink_type_id,))
            return cur.fetchone()

def insert_event(person_id: int, telegram_user_id: int, drink_type_id: int, quantity: int, consumed_at: dt.date):
    t = get_drink_type(drink_type_id)
    if not t:
        raise RuntimeError("Tipo de bebida no encontrado.")

    vol = t["volume_liters"]
    unit_price = float(t["unit_price_eur"])

    volume_total = None if vol is None else float(vol) * quantity
    price_total = unit_price * quantity
    year_start = beer_year_start_for(consumed_at)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO drink_events(
              person_id, telegram_user_id, drink_type_id, quantity, consumed_at,
              year_start, volume_liters_total, price_eur_total, is_void
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE);
            """, (person_id, telegram_user_id, drink_type_id, quantity, consumed_at, year_start, volume_total, price_total))
            conn.commit()

def list_last_events(person_id: int, limit: int = 3):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT e.id, e.quantity, e.consumed_at, dt.label
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id=%s AND e.is_void=FALSE
            ORDER BY e.created_at DESC
            LIMIT %s;
            """, (person_id, limit))
            return cur.fetchall()

def void_event(person_id: int, telegram_user_id: int, event_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE drink_events
            SET is_void=TRUE, voided_at=now(), voided_by_telegram_user_id=%s
            WHERE id=%s AND person_id=%s AND is_void=FALSE
            RETURNING id;
            """, (telegram_user_id, event_id, person_id))
            row = cur.fetchone()
            conn.commit()
            return row is not None

# -------------------------
# Informes / rankings
# -------------------------

def list_years_with_data():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT DISTINCT year_start
            FROM drink_events
            WHERE is_void=FALSE AND year_start IS NOT NULL
            ORDER BY year_start DESC;
            """)
            return [r["year_start"] for r in cur.fetchall()]

def report_year(year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT p.name,
                   COALESCE(SUM(e.quantity),0) AS unidades,
                   COALESCE(SUM(e.volume_liters_total),0) AS litros,
                   COALESCE(SUM(e.price_eur_total),0) AS euros
            FROM persons p
            LEFT JOIN drink_events e
              ON e.person_id=p.id AND e.year_start=%s AND e.is_void=FALSE
            GROUP BY p.name
            ORDER BY euros DESC, litros DESC, unidades DESC;
            """, (year_start,))
            return cur.fetchall()

def report_year_by_drink_for_person(person_id: int, year_start: int):
    """
    Desglose por tipo de bebida para UNA persona y un año cervecero.
    Solo incluye consumos > 0 y eventos no anulados.
    Devuelve: category, label, unidades, litros, euros
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
                dt.category AS category,
                dt.label    AS label,
                SUM(e.quantity) AS unidades,
                COALESCE(SUM(e.volume_liters_total), 0) AS litros,
                COALESCE(SUM(e.price_eur_total), 0) AS euros
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id = %s
              AND e.year_start = %s
              AND e.is_void = FALSE
            GROUP BY dt.category, dt.label
            HAVING SUM(e.quantity) > 0
            ORDER BY
                dt.category ASC,
                euros DESC, litros DESC, unidades DESC, dt.label ASC;
            """, (person_id, year_start))
            return cur.fetchall()

def get_person_year_totals(person_id: int, year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              COALESCE(SUM(quantity),0) AS unidades,
              COALESCE(SUM(volume_liters_total),0) AS litros,
              COALESCE(SUM(price_eur_total),0) AS euros,
              COALESCE(COUNT(*),0) AS eventos
            FROM drink_events
            WHERE person_id=%s AND year_start=%s AND is_void=FALSE;
            """, (person_id, year_start))
            return cur.fetchone()

def is_first_event_of_year(person_id: int, year_start: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT COUNT(*) AS c
            FROM drink_events
            WHERE person_id=%s AND year_start=%s AND is_void=FALSE;
            """, (person_id, year_start))
            return int(cur.fetchone()["c"]) == 1

# -------------------------
# Resumen mensual
# -------------------------

def month_summary(year: int, month: int):
    # Totales del mes por persona (por fecha real consumed_at)
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT p.name,
                   COALESCE(SUM(e.quantity),0) AS unidades,
                   COALESCE(SUM(e.volume_liters_total),0) AS litros,
                   COALESCE(SUM(e.price_eur_total),0) AS euros
            FROM persons p
            LEFT JOIN drink_events e
              ON e.person_id=p.id
             AND e.is_void=FALSE
             AND e.consumed_at >= %s
             AND e.consumed_at < %s
            GROUP BY p.name
            ORDER BY euros DESC, litros DESC, unidades DESC;
            """, (start, end))
            return cur.fetchall()

def monthly_summary_already_sent(year: int, month: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT 1
            FROM monthly_summaries_sent
            WHERE year=%s AND month=%s
            LIMIT 1;
            """, (year, month))
            return cur.fetchone() is not None

def mark_monthly_summary_sent(year: int, month: int) -> bool:
    # devuelve True si lo marcó ahora, False si ya existía
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO monthly_summaries_sent(year, month)
            VALUES (%s,%s)
            ON CONFLICT (year, month) DO NOTHING;
            """, (year, month))
            conn.commit()
            return cur.rowcount > 0

# -------------------------
# ADMIN (opción B)
# -------------------------

def is_admin(telegram_user_id: int) -> bool:
    p = get_assigned_person(telegram_user_id)
    return bool(p and p["name"] == "Pablo")

def add_person(name: str) -> bool:
    name = name.strip()
    if not name:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT DO NOTHING;",
                (name,)
            )
            conn.commit()
            return cur.rowcount > 0

def list_active_persons():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, name
            FROM persons
            WHERE status='ACTIVE'
            ORDER BY name;
            """)
            return cur.fetchall()

def deactivate_person(person_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET status='INACTIVE' WHERE id=%s;", (person_id,))
            conn.commit()
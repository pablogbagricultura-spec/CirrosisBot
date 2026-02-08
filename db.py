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

            # SOLICITUDES PENDIENTES (telegram sin asignar)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_telegrams (
              telegram_user_id BIGINT PRIMARY KEY,
              username TEXT,
              full_name TEXT,
              first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
            SELECT p.id, p.name, p.status
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



# -------------------------
# Solicitudes pendientes (telegram sin asignar)
# -------------------------

def upsert_pending_telegram(telegram_user_id: int, username: str | None, full_name: str | None):
    """Registra/actualiza una solicitud de acceso de un Telegram no asignado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO pending_telegrams(telegram_user_id, username, full_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_user_id)
            DO UPDATE SET
              username = EXCLUDED.username,
              full_name = EXCLUDED.full_name,
              last_seen_at = now();
            """, (telegram_user_id, username, full_name))
            conn.commit()

def list_pending_telegrams(limit: int = 20):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT telegram_user_id, username, full_name, first_seen_at, last_seen_at
            FROM pending_telegrams
            ORDER BY last_seen_at DESC
            LIMIT %s;
            """, (limit,))
            return cur.fetchall()

def delete_pending_telegram(telegram_user_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pending_telegrams WHERE telegram_user_id=%s;", (telegram_user_id,))
            conn.commit()
            return cur.rowcount > 0

def list_active_telegram_user_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT DISTINCT pa.telegram_user_id
            FROM person_accounts pa
            JOIN persons p ON p.id = pa.person_id
            WHERE pa.is_active=TRUE AND p.status='ACTIVE';
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
            SELECT id, label, category, volume_liters, unit_price_eur
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

def list_last_events(person_id: int, limit: int = 5):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT e.id, e.quantity, e.consumed_at, e.created_at, dt.label
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id=%s AND e.is_void=FALSE
            ORDER BY e.created_at DESC
            LIMIT %s;
            """, (person_id, limit))
            return cur.fetchall()




def get_person_beer_units_in_notice_window(person_id: int, now_ts: dt.datetime, reset_hour: int = 9) -> int:
    """Total de 'cervezas' (unidades) para avisos, con reset diario a las {reset_hour}:00.

    Regla:
    - Para avisos, el 'día' empieza a las reset_hour:00 (hora local del timestamp).
    - Todo lo registrado entre 00:00 y reset_hour-1:59 cuenta como el día anterior.
    Cuenta solo eventos:
    - no anulados
    - categoría BEER
    - created_at dentro de [start_ts, end_ts)
    """
    if not isinstance(now_ts, dt.datetime):
        raise TypeError("now_ts debe ser datetime")

    # Si viene sin tzinfo, lo tratamos como 'local naive'
    tz = now_ts.tzinfo
    local_date = now_ts.date()
    if now_ts.hour < reset_hour:
        local_date = local_date - dt.timedelta(days=1)

    start_ts = dt.datetime.combine(local_date, dt.time(reset_hour, 0, 0))
    if tz is not None:
        start_ts = start_ts.replace(tzinfo=tz)
    end_ts = start_ts + dt.timedelta(days=1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT COALESCE(SUM(e.quantity), 0) AS unidades
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id=%s
              AND e.is_void=FALSE
              AND dt.category='BEER'
              AND e.created_at >= %s
              AND e.created_at < %s;
            """, (person_id, start_ts, end_ts))
            row = cur.fetchone()
            return int(row["unidades"] or 0)



def list_user_events_page(person_id: int, limit: int = 15, before_id: int | None = None, after_id: int | None = None):
    """
    Devuelve eventos (bebidas) del usuario con cantidad y hora (created_at), para paginación.
    - Página inicial: before_id=None y after_id=None -> más recientes DESC.
    - Más antiguas: before_id=<id_mas_antiguo_en_pagina> -> siguientes más antiguas DESC.
    - Más recientes: after_id=<id_mas_reciente_en_pagina> -> siguientes más recientes (ASC en DB, luego se invierte).
    Retorna lista de dicts: {id, quantity, label, created_at}
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if before_id is not None and after_id is not None:
                raise ValueError("Usa solo before_id o after_id, no ambos.")

            if before_id is not None:
                cur.execute("""
                SELECT e.id, e.quantity, dt.label, e.created_at
                FROM drink_events e
                JOIN drink_types dt ON dt.id = e.drink_type_id
                WHERE e.person_id=%s AND e.is_void=FALSE AND e.id < %s
                ORDER BY e.id DESC
                LIMIT %s;
                """, (person_id, before_id, limit))
                return cur.fetchall()

            if after_id is not None:
                # Más recientes que after_id (asc) -> el bot las invertirá para mostrar DESC
                cur.execute("""
                SELECT e.id, e.quantity, dt.label, e.created_at
                FROM drink_events e
                JOIN drink_types dt ON dt.id = e.drink_type_id
                WHERE e.person_id=%s AND e.is_void=FALSE AND e.id > %s
                ORDER BY e.id ASC
                LIMIT %s;
                """, (person_id, after_id, limit))
                return cur.fetchall()

            cur.execute("""
            SELECT e.id, e.quantity, dt.label, e.created_at
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id=%s AND e.is_void=FALSE
            ORDER BY e.id DESC
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
            WHERE p.status='ACTIVE'
            GROUP BY p.name
            ORDER BY euros DESC, litros DESC, unidades DESC;
            """, (year_start,))
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
            WHERE p.status='ACTIVE'
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
# Estadísticas vergonzosas (mensuales)
# -------------------------

def _month_range(year: int, month: int):
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start, end

def monthly_shame_report(year: int, month: int, close_liters: float = 0.5):
    """
    Devuelve un dict con estadísticas "vergonzosas" del mes (públicas),
    o None si no aplica (p.ej. menos de 2 personas activas ese mes).

    Reglas:
    - Solo cuenta litros (volume_liters_total). Bebidas sin litros cuentan como 0 para estas stats.
    - Solo personas que hayan bebido al menos 1 vez ese mes (eventos no anulados).
    """
    start, end = _month_range(year, month)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Personas activas del mes (han registrado algo)
            cur.execute("""
            SELECT DISTINCT p.id AS person_id, p.name AS name
            FROM drink_events e
            JOIN persons p ON p.id = e.person_id
            WHERE e.is_void=FALSE
              AND e.consumed_at >= %s
              AND e.consumed_at < %s
              AND p.status='ACTIVE'
            ORDER BY p.name;
            """, (start, end))
            persons = cur.fetchall()

            # Mínimo 2 personas activas para no humillar a alguien solo
            if len(persons) < 2:
                return None

            person_ids = [r["person_id"] for r in persons]

            # Grid día x persona con litros diarios y acumulados
            cur.execute("""
            WITH calendar AS (
              SELECT generate_series(%s::date, (%s::date - interval '1 day'), interval '1 day')::date AS day
            ),
            daily AS (
              SELECT
                e.person_id,
                e.consumed_at AS day,
                SUM(COALESCE(e.volume_liters_total, 0))::numeric AS liters
              FROM drink_events e
              WHERE e.is_void=FALSE
                AND e.consumed_at >= %s
                AND e.consumed_at < %s
                AND e.person_id = ANY(%s)
              GROUP BY e.person_id, e.consumed_at
            ),
            grid AS (
              SELECT
                c.day,
                p.id AS person_id,
                p.name AS name,
                COALESCE(d.liters, 0)::numeric AS liters
              FROM calendar c
              CROSS JOIN (SELECT id, name FROM persons WHERE id = ANY(%s)) p
              LEFT JOIN daily d
                ON d.person_id = p.id AND d.day = c.day
            )
            SELECT
              day,
              person_id,
              name,
              liters,
              SUM(liters) OVER (PARTITION BY person_id ORDER BY day) AS cum_liters
            FROM grid
            ORDER BY day ASC, name ASC;
            """, (start, end, start, end, person_ids, person_ids))
            rows = cur.fetchall()

    # Post-proceso en Python (pocos datos: personas x días)
    days = []
    by_day = {}  # day -> list of dicts (name, liters, cum_liters)
    for r in rows:
        day = r["day"]
        if day not in by_day:
            by_day[day] = []
            days.append(day)
        by_day[day].append({
            "person_id": r["person_id"],
            "name": r["name"],
            "liters": float(r["liters"] or 0),
            "cum_liters": float(r["cum_liters"] or 0),
        })

    def rank_day(entries):
        # ranking por litros acumulados (desc). Desempate por nombre.
        sorted_entries = sorted(entries, key=lambda x: (x["cum_liters"], x["name"]), reverse=True)
        for i, e in enumerate(sorted_entries, 1):
            e["_rank"] = i
        leader = sorted_entries[0] if sorted_entries else None
        return sorted_entries, leader

    leaders = set()
    first_lead_day = {}
    ranks_by_person = {}   # name -> list of ranks over days (solo días con cum>0)
    final_cum = {}         # name -> cum en el último día
    daily_liters_by_day = {}

    for day in days:
        entries = by_day[day]
        daily_liters_by_day[day] = sum(e["liters"] for e in entries)

        ranked, leader = rank_day(entries)
        if leader and leader["cum_liters"] > 0:
            leaders.add(leader["name"])
            first_lead_day.setdefault(leader["name"], day)

        for e in ranked:
            if e["cum_liters"] > 0:
                ranks_by_person.setdefault(e["name"], []).append(e["_rank"])

    last_day = days[-1]
    final_entries, final_leader = rank_day(by_day[last_day])
    final_ranking = [(e["name"], e["cum_liters"]) for e in final_entries]
    for e in final_entries:
        final_cum[e["name"]] = e["cum_liters"]

    # 1) Falso líder: fue líder algún día pero NO termina líder
    false_leader = None
    if final_leader:
        for name in sorted(leaders, key=lambda n: first_lead_day.get(n)):
            if name != final_leader["name"]:
                final_rank = next((i for i, (n, _) in enumerate(final_ranking, 1) if n == name), None)
                false_leader = {"name": name, "first_day": first_lead_day.get(name), "final_rank": final_rank}
                break

    # 2) Mayor caída: mejor rank (mínimo) vs rank final
    biggest_drop = None
    max_drop = 0
    for name, ranks in ranks_by_person.items():
        if not ranks:
            continue
        best_rank = min(ranks)
        final_rank = next((i for i, (n, _) in enumerate(final_ranking, 1) if n == name), None)
        if final_rank is None:
            continue
        drop = final_rank - best_rank
        if drop > max_drop:
            max_drop = drop
            biggest_drop = {"name": name, "best_rank": best_rank, "final_rank": final_rank, "drop": drop}

    # 3) Casi campeón: días a <= close_liters del líder sin ser líder
    almost_counts = {}
    for day in days:
        ranked, leader = rank_day(by_day[day])
        if not leader or leader["cum_liters"] <= 0:
            continue
        leader_name = leader["name"]
        leader_c = leader["cum_liters"]
        for e in ranked[1:]:
            if e["cum_liters"] <= 0 or e["name"] == leader_name:
                continue
            if leader_c - e["cum_liters"] <= close_liters:
                almost_counts[e["name"]] = almost_counts.get(e["name"], 0) + 1

    almost_champion = None
    if almost_counts:
        name = max(almost_counts.keys(), key=lambda n: (almost_counts[n], float(final_cum.get(n, 0)), n))
        almost_champion = {"name": name, "times": almost_counts[name]}

    # 4) Fantasma: más días en blanco (solo entre los que han bebido alguna vez ese mes)
    days_in_month = len(days)
    blank_counts = {}
    for name in final_cum.keys():
        blank = 0
        for day in days:
            entry = next((e for e in by_day[day] if e["name"] == name), None)
            if entry and entry["liters"] == 0:
                blank += 1
        blank_counts[name] = blank

    ghost = None
    if blank_counts:
        gname = max(blank_counts.keys(), key=lambda n: (blank_counts[n], n))
        ghost = {"name": gname, "blank_days": blank_counts[gname], "days": days_in_month}

    # 5) Semana más triste (lunes-domingo) dentro del rango del mes
    week_totals = {}
    for day, total in daily_liters_by_day.items():
        week_start = day - dt.timedelta(days=day.weekday())  # lunes
        week_totals[week_start] = week_totals.get(week_start, 0.0) + float(total)

    saddest_week = None
    if week_totals:
        w = min(week_totals.keys(), key=lambda d: (week_totals[d], d))
        saddest_week = {"week_start": w, "liters": float(week_totals[w])}

    return {
        "year": year,
        "month": month,
        "final_leader": final_leader["name"] if final_leader else None,
        "final_ranking": final_ranking,  # list[(name, liters)]
        "false_leader": false_leader,
        "biggest_drop": biggest_drop,
        "almost_champion": almost_champion,
        "ghost": ghost,
        "saddest_week": saddest_week,
    }

def person_year_breakdown(person_id: int, year_start: int):
    """
    Desglose por tipo de bebida para 1 persona en un año cervecero.
    Devuelve filas: category, label, unidades, litros, euros, has_liters
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              dt.category AS category,
              dt.label AS label,
              COALESCE(SUM(e.quantity), 0) AS unidades,
              COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros,
              COALESCE(SUM(e.price_eur_total), 0) AS euros,
              (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id = %s
              AND e.year_start = %s
              AND e.is_void = FALSE
            GROUP BY dt.category, dt.label, dt.volume_liters
            HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY dt.category ASC, litros DESC, euros DESC, unidades DESC, dt.label ASC;
            """, (person_id, year_start))
            return cur.fetchall()


def year_drinks_totals(year_start: int):
    """
    Totales del año por bebida (global, todos).
    Devuelve: category, label, unidades, litros, euros, has_liters
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              dt.category AS category,
              dt.label AS label,
              COALESCE(SUM(e.quantity), 0) AS unidades,
              COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros,
              COALESCE(SUM(e.price_eur_total), 0) AS euros,
              (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.year_start = %s
              AND e.is_void = FALSE
            GROUP BY dt.category, dt.label, dt.volume_liters
            HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY litros DESC, unidades DESC, dt.label ASC;
            """, (year_start,))
            return cur.fetchall()


def year_drink_type_person_totals(year_start: int):
    """
    Totales por (bebida x persona) en el año.
    Devuelve: category, label, person_name, unidades, litros, has_liters
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              dt.category AS category,
              dt.label AS label,
              p.name AS person_name,
              COALESCE(SUM(e.quantity), 0) AS unidades,
              COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros,
              (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            JOIN persons p ON p.id = e.person_id
            WHERE e.year_start = %s
              AND e.is_void = FALSE
              AND p.status='ACTIVE'
            GROUP BY dt.category, dt.label, p.name, dt.volume_liters
            HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY dt.category ASC, dt.label ASC, litros DESC, unidades DESC, p.name ASC;
            """, (year_start,))
            return cur.fetchall()


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


# -------------------------
# ADMIN: Personas (histórico) + asignaciones manuales
# -------------------------

def list_persons_by_status(status: str):
    status = status.upper()
    if status not in ("NEW", "ACTIVE", "INACTIVE"):
        raise ValueError("Estado inválido")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, name, status, created_at
            FROM persons
            WHERE status=%s
            ORDER BY name;
            """, (status,))
            return cur.fetchall()

def list_persons_without_active_telegram():
    """Plazas sin telegram activo (da igual el status)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT p.id, p.name, p.status, p.created_at
            FROM persons p
            LEFT JOIN person_accounts pa
              ON pa.person_id = p.id AND pa.is_active = TRUE
            WHERE pa.id IS NULL
            ORDER BY p.name;
            """)
            return cur.fetchall()

def search_persons_by_name(q: str, limit: int = 20):
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, name, status, created_at
            FROM persons
            WHERE name ILIKE %s
            ORDER BY name
            LIMIT %s;
            """, (like, limit))
            return cur.fetchall()

def get_person_profile(person_id: int):
    """Devuelve ficha completa: persona + telegram activo + historico + stats."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT id, name, status, created_at
            FROM persons
            WHERE id=%s;
            """, (person_id,))
            person = cur.fetchone()
            if not person:
                return None

            cur.execute("""
            SELECT telegram_user_id, assigned_at
            FROM person_accounts
            WHERE person_id=%s AND is_active=TRUE
            ORDER BY assigned_at DESC
            LIMIT 1;
            """, (person_id,))
            active_account = cur.fetchone()

            cur.execute("""
            SELECT telegram_user_id, assigned_at, unassigned_at
            FROM person_accounts
            WHERE person_id=%s AND is_active=FALSE
            ORDER BY unassigned_at DESC NULLS LAST, assigned_at DESC;
            """, (person_id,))
            previous_accounts = cur.fetchall()

            # Stats (NO anulados)
            cur.execute("""
            SELECT
              COALESCE(COUNT(*),0) AS events_count,
              MAX(consumed_at) AS last_activity_at
            FROM drink_events
            WHERE person_id=%s AND is_void=FALSE;
            """, (person_id,))
            stats = cur.fetchone() or {"events_count": 0, "last_activity_at": None}

            return {
                "person": person,
                "active_account": active_account,
                "previous_accounts": previous_accounts,
                "stats": stats,
            }

def admin_assign_telegram_to_person(person_id: int, telegram_user_id: int):
    """Asigna un telegram (pendiente o manual) a una persona. Reasigna si ya tenía otro."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Bloquea persona
            cur.execute("SELECT id, status FROM persons WHERE id=%s FOR UPDATE;", (person_id,))
            p = cur.fetchone()
            if not p:
                conn.rollback()
                return ("NOT_FOUND", None)

            # Si el telegram ya está asignado a otra persona activa, error
            cur.execute("""
            SELECT person_id
            FROM person_accounts
            WHERE telegram_user_id=%s AND is_active=TRUE
            LIMIT 1;
            """, (telegram_user_id,))
            row = cur.fetchone()
            if row and int(row["person_id"]) != int(person_id):
                conn.rollback()
                return ("TG_TAKEN", None)

            # Desactiva asignación activa previa de la persona (si existía)
            cur.execute("""
            UPDATE person_accounts
            SET is_active=FALSE, unassigned_at=now()
            WHERE person_id=%s AND is_active=TRUE;
            """, (person_id,))

            # Inserta nueva asignación
            cur.execute("""
            INSERT INTO person_accounts(person_id, telegram_user_id, is_active)
            VALUES (%s,%s,TRUE);
            """, (person_id, telegram_user_id))

            # La persona pasa a ACTIVE (aunque viniera INACTIVE)
            cur.execute("UPDATE persons SET status='ACTIVE' WHERE id=%s;", (person_id,))

            # Borra solicitud pendiente (si existía)
            cur.execute("DELETE FROM pending_telegrams WHERE telegram_user_id=%s;", (telegram_user_id,))

            conn.commit()
            return ("OK", None)

def admin_suspend_person(person_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET status='INACTIVE' WHERE id=%s;", (person_id,))
            conn.commit()
            return cur.rowcount > 0

def admin_reactivate_person(person_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET status='ACTIVE' WHERE id=%s;", (person_id,))
            conn.commit()
            return cur.rowcount > 0

def admin_delete_person(person_id: int) -> bool:
    """Elimina persona/plaza y TODO lo asociado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Borra dependencias primero
            cur.execute("DELETE FROM drink_events WHERE person_id=%s;", (person_id,))
            cur.execute("DELETE FROM person_accounts WHERE person_id=%s;", (person_id,))
            cur.execute("DELETE FROM persons WHERE id=%s;", (person_id,))
            conn.commit()
            return cur.rowcount > 0


# ---------------- CALENDAR PERIOD RANKING (used by Ranking UI) ----------------
import calendar as _calendar

def list_calendar_years_with_data():
    """Calendar years based on consumed_at."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM consumed_at)::INT AS y
            FROM drink_events
            WHERE is_void=FALSE
            ORDER BY y DESC;
            """)
            return [r["y"] for r in cur.fetchall()]

def user_stats_range(start_date: dt.date, end_date: dt.date):
    """
    Stats per ACTIVE person for a calendar date range.
    - liters_total: sum(volume_liters_total) (only BEER contributes)
    - active_days: count of distinct consumed_at days with any event
    - strong_days: count of days where liters_day >= 3.0 (per person)
    - peak_day / peak_liters: day with max liters_day (per person)
    Returns list sorted by liters_total desc.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            WITH active_days AS (
              SELECT e.person_id, e.consumed_at::date AS d
              FROM drink_events e
              WHERE e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
              GROUP BY e.person_id, d
            ),
            liters_by_day AS (
              SELECT e.person_id, e.consumed_at::date AS d,
                     COALESCE(SUM(e.volume_liters_total), 0) AS liters_day
              FROM drink_events e
              WHERE e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
              GROUP BY e.person_id, d
            ),
            strong_days AS (
              SELECT person_id, COUNT(*)::INT AS strong_days
              FROM liters_by_day
              WHERE liters_day >= 3.0
              GROUP BY person_id
            ),
            peak AS (
              SELECT DISTINCT ON (person_id)
                     person_id,
                     d AS peak_day,
                     liters_day AS peak_liters
              FROM liters_by_day
              ORDER BY person_id, liters_day DESC, d DESC
            ),
            totals AS (
              SELECT p.id AS person_id,
                     p.name AS person,
                     COALESCE(SUM(e.volume_liters_total), 0) AS liters_total
              FROM persons p
              JOIN drink_events e ON e.person_id=p.id
              WHERE p.status='ACTIVE' AND e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
              GROUP BY p.id, p.name
            ),
            act AS (
              SELECT person_id, COUNT(*)::INT AS active_days
              FROM active_days
              GROUP BY person_id
            )
            SELECT
              t.person_id,
              t.person,
              t.liters_total,
              COALESCE(a.active_days, 0) AS active_days,
              COALESCE(s.strong_days, 0) AS strong_days,
              pk.peak_day AS peak_day,
              COALESCE(pk.peak_liters, 0) AS peak_liters
            FROM totals t
            LEFT JOIN act a ON a.person_id=t.person_id
            LEFT JOIN strong_days s ON s.person_id=t.person_id
            LEFT JOIN peak pk ON pk.person_id=t.person_id
            ORDER BY t.liters_total DESC, t.person ASC;
            """, (start_date, end_date, start_date, end_date, start_date, end_date))
            rows = cur.fetchall()

    out = []
    for r in rows:
        active_days = int(r["active_days"])
        liters_total = float(r["liters_total"] or 0)
        avg_active = (liters_total / active_days) if active_days else 0.0
        out.append({
            "person_id": r["person_id"],
            "person": r["person"],
            "liters_total": liters_total,
            "active_days": active_days,
            "avg_liters_per_active_day": avg_active,
            "strong_days": int(r["strong_days"] or 0),
            "peak_day": r["peak_day"],
            "peak_liters": float(r["peak_liters"] or 0),
        })
    return out

def user_year_stats(year: int):
    """
    Stats per ACTIVE person for a calendar year.
    Adds: avg_liters_per_calendar_day, strongest/weakest month by liters.
    """
    start_date = dt.date(year, 1, 1)
    end_date = dt.date(year, 12, 31)
    days_in_year = 366 if _calendar.isleap(year) else 365

    base = user_stats_range(start_date, end_date)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            WITH monthly AS (
              SELECT e.person_id,
                     EXTRACT(MONTH FROM e.consumed_at)::INT AS m,
                     COALESCE(SUM(e.volume_liters_total), 0) AS liters_m
              FROM drink_events e
              WHERE e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
              GROUP BY e.person_id, m
            )
            SELECT p.id AS person_id,
                   m.m AS m,
                   COALESCE(m.liters_m, 0) AS liters_m
            FROM persons p
            LEFT JOIN monthly m ON m.person_id=p.id
            WHERE p.status='ACTIVE'
            ORDER BY p.id, m.m;
            """, (start_date, end_date))
            rows = cur.fetchall()

    monthly_map = {}
    for r in rows:
        pid = r["person_id"]
        if pid not in monthly_map:
            monthly_map[pid] = {i: 0.0 for i in range(1, 13)}
        if r["m"] is not None:
            monthly_map[pid][int(r["m"])] = float(r["liters_m"] or 0)

    for item in base:
        pid = item["person_id"]
        liters_total = float(item["liters_total"])
        item["avg_liters_per_calendar_day"] = liters_total / days_in_year

        months = monthly_map.get(pid, {i: 0.0 for i in range(1, 13)})
        strongest_m = max(months.items(), key=lambda kv: (kv[1], kv[0]))[0]
        weakest_m = min(months.items(), key=lambda kv: (kv[1], kv[0]))[0]
        item["strongest_month"] = strongest_m
        item["strongest_month_liters"] = months[strongest_m]
        item["weakest_month"] = weakest_m
        item["weakest_month_liters"] = months[weakest_m]
    return base

def group_month_summary(year: int):
    """
    Global monthly summaries for a calendar year (group totals).
    strong day: group liters_day >= 3.0
    """
    out = []
    for m in range(1, 13):
        days_in_month = _calendar.monthrange(year, m)[1]
        start_date = dt.date(year, m, 1)
        end_date = dt.date(year, m, days_in_month)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                WITH active_days AS (
                  SELECT e.consumed_at::date AS d
                  FROM drink_events e
                  WHERE e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
                  GROUP BY d
                ),
                liters_by_day AS (
                  SELECT e.consumed_at::date AS d,
                         COALESCE(SUM(e.volume_liters_total), 0) AS liters_day
                  FROM drink_events e
                  WHERE e.is_void=FALSE AND e.consumed_at BETWEEN %s AND %s
                  GROUP BY d
                )
                SELECT
                  COALESCE((SELECT SUM(volume_liters_total) FROM drink_events WHERE is_void=FALSE AND consumed_at BETWEEN %s AND %s), 0) AS liters_total,
                  COALESCE((SELECT COUNT(*) FROM active_days), 0)::INT AS active_days,
                  COALESCE((SELECT COUNT(*) FROM liters_by_day WHERE liters_day >= 3.0), 0)::INT AS strong_days
                """, (start_date, end_date, start_date, end_date, start_date, end_date))
                r = cur.fetchone()

        liters_total = float(r["liters_total"] or 0)
        active_days = int(r["active_days"] or 0)
        strong_days = int(r["strong_days"] or 0)
        avg_active = (liters_total / active_days) if active_days else 0.0
        avg_calendar = liters_total / days_in_month
        zero_days = days_in_month - active_days

        out.append({
            "month": m,
            "liters_total": liters_total,
            "active_days": active_days,
            "avg_per_active_day": avg_active,
            "avg_per_calendar_day": avg_calendar,
            "zero_days": zero_days,
            "strong_days": strong_days,
            "days_in_month": days_in_month,
        })
    return out

def drink_type_person_totals_range(start_date: dt.date, end_date: dt.date):
    """Totals per (drink x person) in date range."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              dt.category AS category,
              dt.label AS label,
              p.name AS person,
              COALESCE(SUM(e.quantity), 0)::INT AS unidades,
              COALESCE(SUM(e.volume_liters_total), 0) AS litros,
              (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            JOIN persons p ON p.id = e.person_id
            WHERE e.is_void=FALSE
              AND e.consumed_at BETWEEN %s AND %s
              AND p.status='ACTIVE'
            GROUP BY dt.category, dt.label, p.name, dt.volume_liters
            HAVING COALESCE(SUM(e.quantity),0) > 0
            ORDER BY dt.category, dt.label, litros DESC, unidades DESC, p.name ASC;
            """, (start_date, end_date))
            return cur.fetchall()

def drink_type_totals_range(start_date: dt.date, end_date: dt.date):
    """Totals per drink (global) in date range."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT
              dt.category AS category,
              dt.label AS label,
              COALESCE(SUM(e.quantity), 0)::INT AS unidades,
              COALESCE(SUM(e.volume_liters_total), 0) AS litros,
              (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e
            JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.is_void=FALSE
              AND e.consumed_at BETWEEN %s AND %s
            GROUP BY dt.category, dt.label, dt.volume_liters
            HAVING COALESCE(SUM(e.quantity),0) > 0
            ORDER BY dt.category, dt.label;
            """, (start_date, end_date))
            return cur.fetchall()

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
    jan7 = dt.date(d.year, 1, 7)
    return d.year if d >= jan7 else (d.year - 1)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS persons (
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK (status IN ('NEW','ACTIVE','INACTIVE')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
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
            cur.execute("""
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS year_start INT;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS volume_liters_total NUMERIC(10,3);
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS price_eur_total NUMERIC(10,2);
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS is_void BOOLEAN NOT NULL DEFAULT FALSE;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ;
            ALTER TABLE drink_events ADD COLUMN IF NOT EXISTS voided_by_telegram_user_id BIGINT;
            """)
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_person_recent
              ON drink_events(person_id, is_void, created_at DESC);
            """)
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_year
              ON drink_events(year_start, is_void);
            """)
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

        with conn.cursor() as cur:
            for name in PERSONS_SEED:
                cur.execute("INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT (name) DO NOTHING;", (name,))
            for code, label, cat, vol, price in DRINKS_SEED:
                cur.execute("""
                    INSERT INTO drink_types(code,label,category,volume_liters,unit_price_eur,is_active)
                    VALUES (%s,%s,%s,%s,%s,TRUE)
                    ON CONFLICT (code) DO NOTHING;
                """, (code, label, cat, vol, price))
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
            cur.execute("SELECT id, name FROM persons WHERE status='NEW' ORDER BY name;")
            return cur.fetchall()

def assign_person(telegram_user_id: int, person_id: int):
    existing = get_assigned_person(telegram_user_id)
    if existing: return ("ALREADY", existing)
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, status FROM persons WHERE id=%s FOR UPDATE;", (person_id,))
                row = cur.fetchone()
                if not row or row["status"] != "NEW":
                    conn.rollback()
                    return ("TAKEN", None)
                cur.execute("UPDATE persons SET status='ACTIVE' WHERE id=%s;", (person_id,))
                cur.execute("INSERT INTO person_accounts(person_id, telegram_user_id, is_active) VALUES (%s,%s,TRUE);", (person_id, telegram_user_id))
                conn.commit()
                return ("OK", {"id": row["id"], "name": row["name"]})
        except psycopg2.Error:
            conn.rollback()
            return ("TAKEN", None)

def list_active_telegram_user_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT telegram_user_id FROM person_accounts WHERE is_active=TRUE;")
            return [r["telegram_user_id"] for r in cur.fetchall()]

def list_drink_types(category: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, label FROM drink_types WHERE is_active=TRUE AND category=%s ORDER BY label;", (category,))
            return cur.fetchall()

def get_drink_type(drink_type_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, label, volume_liters, unit_price_eur FROM drink_types WHERE id=%s;", (drink_type_id,))
            return cur.fetchone()

def insert_event(person_id: int, telegram_user_id: int, drink_type_id: int, quantity: int, consumed_at: dt.date):
    t = get_drink_type(drink_type_id)
    if not t: raise RuntimeError("Tipo de bebida no encontrado.")
    vol = t["volume_liters"]
    unit_price = float(t["unit_price_eur"])
    volume_total = None if vol is None else float(vol) * quantity
    price_total = unit_price * quantity
    year_start = beer_year_start_for(consumed_at)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO drink_events(person_id, telegram_user_id, drink_type_id, quantity, consumed_at, year_start, volume_liters_total, price_eur_total, is_void)
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
            ORDER BY e.created_at DESC LIMIT %s;
            """, (person_id, limit))
            return cur.fetchall()

def void_event(person_id: int, telegram_user_id: int, event_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE drink_events SET is_void=TRUE, voided_at=now(), voided_by_telegram_user_id=%s
            WHERE id=%s AND person_id=%s AND is_void=FALSE RETURNING id;
            """, (telegram_user_id, event_id, person_id))
            row = cur.fetchone()
            conn.commit()
            return row is not None

def list_years_with_data():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT year_start FROM drink_events WHERE is_void=FALSE AND year_start IS NOT NULL ORDER BY year_start DESC;")
            return [r["year_start"] for r in cur.fetchall()]

def report_year(year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT p.name, COALESCE(SUM(e.quantity),0) AS unidades, COALESCE(SUM(e.volume_liters_total),0) AS litros, COALESCE(SUM(e.price_eur_total),0) AS euros
            FROM persons p
            LEFT JOIN drink_events e ON e.person_id=p.id AND e.year_start=%s AND e.is_void=FALSE
            GROUP BY p.name ORDER BY litros DESC, euros DESC, unidades DESC;
            """, (year_start,))
            return cur.fetchall()

def get_person_year_totals(person_id: int, year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT COALESCE(SUM(quantity),0) AS unidades, COALESCE(SUM(volume_liters_total),0) AS litros, COALESCE(SUM(price_eur_total),0) AS euros, COALESCE(COUNT(*),0) AS eventos
            FROM drink_events WHERE person_id=%s AND year_start=%s AND is_void=FALSE;
            """, (person_id, year_start))
            return cur.fetchone()

def is_first_event_of_year(person_id: int, year_start: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM drink_events WHERE person_id=%s AND year_start=%s AND is_void=FALSE;", (person_id, year_start))
            return int(cur.fetchone()["c"]) == 1

def month_summary(year: int, month: int):
    start = dt.date(year, month, 1)
    end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT p.name, COALESCE(SUM(e.quantity),0) AS unidades, COALESCE(SUM(e.volume_liters_total),0) AS litros, COALESCE(SUM(e.price_eur_total),0) AS euros
            FROM persons p
            LEFT JOIN drink_events e ON e.person_id=p.id AND e.is_void=FALSE AND e.consumed_at >= %s AND e.consumed_at < %s
            GROUP BY p.name ORDER BY litros DESC, euros DESC, unidades DESC;
            """, (start, end))
            return cur.fetchall()

def monthly_summary_already_sent(year: int, month: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM monthly_summaries_sent WHERE year=%s AND month=%s LIMIT 1;", (year, month))
            return cur.fetchone() is not None

def mark_monthly_summary_sent(year: int, month: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO monthly_summaries_sent(year, month) VALUES (%s,%s) ON CONFLICT (year, month) DO NOTHING;", (year, month))
            conn.commit()
            return cur.rowcount > 0

def monthly_shame_report(year: int, month: int, close_liters: float = 0.5):
    start = dt.date(year, month, 1)
    end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT DISTINCT p.id AS person_id, p.name AS name FROM drink_events e JOIN persons p ON p.id = e.person_id
            WHERE e.is_void=FALSE AND e.consumed_at >= %s AND e.consumed_at < %s ORDER BY p.name;
            """, (start, end))
            persons = cur.fetchall()
            if len(persons) < 2: return None
            person_ids = [r["person_id"] for r in persons]
            cur.execute("""
            WITH calendar AS (SELECT generate_series(%s::date, (%s::date - interval '1 day'), interval '1 day')::date AS day),
            daily AS (SELECT e.person_id, e.consumed_at AS day, SUM(COALESCE(e.volume_liters_total, 0))::numeric AS liters FROM drink_events e
                      WHERE e.is_void=FALSE AND e.consumed_at >= %s AND e.consumed_at < %s AND e.person_id = ANY(%s) GROUP BY e.person_id, e.consumed_at),
            grid AS (SELECT c.day, p.id AS person_id, p.name AS name, COALESCE(d.liters, 0)::numeric AS liters FROM calendar c
                     CROSS JOIN (SELECT id, name FROM persons WHERE id = ANY(%s)) p LEFT JOIN daily d ON d.person_id = p.id AND d.day = c.day)
            SELECT day, person_id, name, liters, SUM(liters) OVER (PARTITION BY person_id ORDER BY day) AS cum_liters FROM grid ORDER BY day ASC, name ASC;
            """, (start, end, start, end, person_ids, person_ids))
            rows = cur.fetchall()
    days, by_day = [], {}
    for r in rows:
        day = r["day"]
        if day not in by_day:
            by_day[day] = []
            days.append(day)
        by_day[day].append({"person_id": r["person_id"], "name": r["name"], "liters": float(r["liters"] or 0), "cum_liters": float(r["cum_liters"] or 0)})
    def rank_day(entries):
        sorted_entries = sorted(entries, key=lambda x: (x["cum_liters"], x["name"]), reverse=True)
        for i, e in enumerate(sorted_entries, 1): e["_rank"] = i
        return sorted_entries, (sorted_entries[0] if sorted_entries else None)
    leaders, first_lead_day, ranks_by_person, final_cum, daily_liters_by_day = set(), {}, {}, {}, {}
    for day in days:
        entries = by_day[day]
        daily_liters_by_day[day] = sum(e["liters"] for e in entries)
        ranked, leader = rank_day(entries)
        if leader and leader["cum_liters"] > 0:
            leaders.add(leader["name"])
            first_lead_day.setdefault(leader["name"], day)
        for e in ranked:
            if e["cum_liters"] > 0: ranks_by_person.setdefault(e["name"], []).append(e["_rank"])
    last_day = days[-1]
    final_entries, final_leader = rank_day(by_day[last_day])
    final_ranking = [(e["name"], e["cum_liters"]) for e in final_entries]
    for e in final_entries: final_cum[e["name"]] = e["cum_liters"]
    false_leader = None
    if final_leader:
        for name in sorted(leaders, key=lambda n: first_lead_day.get(n)):
            if name != final_leader["name"]:
                false_leader = {"name": name, "first_day": first_lead_day.get(name), "final_rank": next((i for i, (n, _) in enumerate(final_ranking, 1) if n == name), None)}
                break
    biggest_drop, max_drop = None, 0
    for name, ranks in ranks_by_person.items():
        best_rank = min(ranks)
        final_rank = next((i for i, (n, _) in enumerate(final_ranking, 1) if n == name), None)
        if final_rank and (final_rank - best_rank) > max_drop:
            max_drop = final_rank - best_rank
            biggest_drop = {"name": name, "best_rank": best_rank, "final_rank": final_rank, "drop": max_drop}
    almost_counts = {}
    for day in days:
        ranked, leader = rank_day(by_day[day])
        if leader and leader["cum_liters"] > 0:
            for e in ranked[1:]:
                if e["cum_liters"] > 0 and (leader["cum_liters"] - e["cum_liters"]) <= close_liters:
                    almost_counts[e["name"]] = almost_counts.get(e["name"], 0) + 1
    almost_champion = None
    if almost_counts:
        name = max(almost_counts.keys(), key=lambda n: (almost_counts[n], float(final_cum.get(n, 0)), n))
        almost_champion = {"name": name, "times": almost_counts[name]}
    blank_counts = {name: sum(1 for day in days if next((e for e in by_day[day] if e["name"] == name), {"liters": 0})["liters"] == 0) for name in final_cum.keys()}
    ghost = {"name": max(blank_counts.keys(), key=lambda n: (blank_counts[n], n)), "blank_days": blank_counts[max(blank_counts.keys(), key=lambda n: (blank_counts[n], n))], "days": len(days)} if blank_counts else None
    week_totals = {}
    for day, total in daily_liters_by_day.items():
        ws = day - dt.timedelta(days=day.weekday())
        week_totals[ws] = week_totals.get(ws, 0.0) + float(total)
    saddest_week = {"week_start": min(week_totals.keys(), key=lambda d: (week_totals[d], d)), "liters": float(week_totals[min(week_totals.keys(), key=lambda d: (week_totals[d], d))])} if week_totals else None
    return {"year": year, "month": month, "final_leader": final_leader["name"] if final_leader else None, "final_ranking": final_ranking, "false_leader": false_leader, "biggest_drop": biggest_drop, "almost_champion": almost_champion, "ghost": ghost, "saddest_week": saddest_week}

def person_year_breakdown(person_id: int, year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT dt.category, dt.label, COALESCE(SUM(e.quantity), 0) AS unidades, COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros, COALESCE(SUM(e.price_eur_total), 0) AS euros, (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.person_id = %s AND e.year_start = %s AND e.is_void = FALSE
            GROUP BY dt.category, dt.label, dt.volume_liters HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY dt.category ASC, litros DESC, euros DESC, unidades DESC, dt.label ASC;
            """, (person_id, year_start))
            return cur.fetchall()

def year_drinks_totals(year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT dt.category, dt.label, COALESCE(SUM(e.quantity), 0) AS unidades, COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros, COALESCE(SUM(e.price_eur_total), 0) AS euros, (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e JOIN drink_types dt ON dt.id = e.drink_type_id
            WHERE e.year_start = %s AND e.is_void = FALSE
            GROUP BY dt.category, dt.label, dt.volume_liters HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY litros DESC, unidades DESC, dt.label ASC;
            """, (year_start,))
            return cur.fetchall()

def year_drink_type_person_totals(year_start: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT dt.category, dt.label, p.name AS person_name, COALESCE(SUM(e.quantity), 0) AS unidades, COALESCE(SUM(COALESCE(e.volume_liters_total, 0)), 0) AS litros, (dt.volume_liters IS NOT NULL) AS has_liters
            FROM drink_events e JOIN drink_types dt ON dt.id = e.drink_type_id JOIN persons p ON p.id = e.person_id
            WHERE e.year_start = %s AND e.is_void = FALSE
            GROUP BY dt.category, dt.label, p.name, dt.volume_liters HAVING COALESCE(SUM(e.quantity), 0) > 0
            ORDER BY dt.category ASC, dt.label ASC, litros DESC, unidades DESC, p.name ASC;
            """, (year_start, ))
            return cur.fetchall()

def is_admin(telegram_user_id: int) -> bool:
    p = get_assigned_person(telegram_user_id)
    return bool(p and p["name"] == "Pablo")

def add_person(name: str) -> bool:
    name = name.strip()
    if not name: return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO persons(name, status) VALUES (%s, 'NEW') ON CONFLICT DO NOTHING;", (name,))
            conn.commit()
            return cur.rowcount > 0

def list_active_persons():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM persons WHERE status='ACTIVE' ORDER BY name;")
            return cur.fetchall()

def deactivate_person(person_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET status='INACTIVE' WHERE id=%s;", (person_id,))
            conn.commit()
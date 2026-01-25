import os
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS persons (
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              created_at TIMESTAMPTZ DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS drink_types (
              id SERIAL PRIMARY KEY,
              code TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL,
              category TEXT NOT NULL,
              volume_liters NUMERIC,
              unit_price_eur NUMERIC,
              is_active BOOLEAN DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS drink_events (
              id SERIAL PRIMARY KEY,
              person_id INT,
              drink_type_id INT,
              quantity INT NOT NULL,
              consumed_at DATE NOT NULL,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)
            conn.commit()

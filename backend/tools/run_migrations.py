"""One-shot DB connectivity + migration runner. Usage: python tools/run_migrations.py"""
import re
import sys
from pathlib import Path

import psycopg2

BACKEND = Path(__file__).resolve().parent.parent


def load_db_url() -> str:
    env = (BACKEND / ".env").read_text(encoding="utf-8")
    match = re.search(r"^DATABASE_URL=(\S+)", env, re.M)
    if not match:
        raise SystemExit("DATABASE_URL missing in backend/.env")
    return match.group(1)


def main() -> None:
    url = load_db_url()
    conn = psycopg2.connect(url, sslmode="require", connect_timeout=25)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("select current_database(), current_user")
    print("connected as", cur.fetchone())

    migrations = sorted((BACKEND / "migrations").glob("*.sql"))
    for path in migrations:
        sql = path.read_text(encoding="utf-8")
        cur.execute(sql)
        conn.commit()
        print("applied", path.name)

    cur.execute(
        "select table_schema || '.' || table_name from information_schema.tables "
        "where table_schema in ('public','auth') and table_name in ('profiles','auth_users','users')"
        " order by 1"
    )
    rows = cur.fetchall()
    print("key tables present:", rows)
    cur.execute("select count(*) from public.audit_events")
    print("audit_events rows:", cur.fetchone()[0])
    cur.execute("select public.verify_audit_chain()")
    broken = cur.fetchall()
    print("audit chain problems:", broken if broken else "none")
    cur.close()
    conn.close()
    print("OK")


if __name__ == "__main__":
    try:
        main()
    except psycopg2.Error as exc:
        print("DB ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)

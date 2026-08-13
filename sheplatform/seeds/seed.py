"""Initial data seeding (guide 3, seeds/seed.py)."""
from __future__ import annotations

from sheplatform.core import auth
from sheplatform.database import get_db, init_db


def seed() -> None:
    init_db()
    db = get_db()
    try:
        # Organisation (Econet - single org in dev)
        db.execute(
            "INSERT OR IGNORE INTO organisations (name, slug) VALUES (%s, %s)",
            ("Econet Wireless Zimbabwe", "econet"),
        )
        org = db.execute("SELECT id FROM organisations WHERE slug = 'econet'").fetchone()
        org_id = org["id"]

        # Seed users: one per key role (password: ChangeMe!123)
        users = [
            ("superadmin@she.local", "Super", "Admin", "super_admin"),
            ("manager@she.local", "SHE", "Manager", "she_manager"),
            ("officer@she.local", "SHE", "Officer", "she_officer"),
            ("champion@she.local", "SHE", "Champion", "she_champion"),
            ("cro@she.local", "Chief", "Risk Officer", "cro"),
            ("employee@she.local", "General", "Employee", "employee"),
        ]
        for email, first, last, role in users:
            exists = db.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
            if exists:
                continue
            db.execute(
                "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (email, auth.hash_password("ChangeMe!123"), first, last, role, org_id),
            )
        db.commit()

        # Sites (Econet locations for benchmarking)
        sites = [
            ("HRE-HQ", "Harare HQ", "Harare", "office"),
            ("BYO-HQ", "Bulawayo HQ", "Bulawayo", "office"),
            ("MUT", "Mutare Branch", "Mutare", "retail"),
            ("GWE", "Gweru Branch", "Gweru", "retail"),
            ("HRE-DC", "Harare Data Centre", "Harare", "facility"),
            ("BYO-T1", "Bulawayo Tower 1", "Bulawayo", "tower"),
        ]
        for code, name, city, stype in sites:
            db.execute(
                "INSERT OR IGNORE INTO sites (site_code, site_name, city, site_type) "
                "VALUES (%s, %s, %s, %s)", (code, name, city, stype))
        db.commit()
        print("Seeded organisation + users (password for all: ChangeMe!123)")
    finally:
        db.close()


if __name__ == "__main__":
    seed()

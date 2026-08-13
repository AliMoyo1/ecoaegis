"""Chemicals / SDS register tests."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


class TestChemicals:
    def test_create_and_list(self, db):
        officer = _mk_user(db, "she_officer", "chem1@test.com")
        from sheplatform.modules.chemicals.data_service import create_chemical, list_chemicals
        chem = create_chemical(
            db, name="Diesel", cas_number="68476-34-6", supplier="Total Energies",
            hazard_class="flammable", sds_path="docs/SDS/diesel.pdf",
            storage_location="Generator room", created_by=officer["id"])
        assert chem["chem_ref"].startswith("CHEM-")

        items = list_chemicals(db, hazard_class="flammable")
        assert any(c["name"] == "Diesel" for c in items)

    def test_invalid_hazard_class(self, db):
        officer = _mk_user(db, "she_officer", "chem2@test.com")
        from sheplatform.modules.chemicals.data_service import create_chemical
        with pytest.raises(ValueError):
            create_chemical(db, name="X", hazard_class="nuclear", created_by=officer["id"])

    def test_hazard_summary(self, db):
        officer = _mk_user(db, "she_officer", "chem3@test.com")
        from sheplatform.modules.chemicals.data_service import (
            create_chemical, hazard_summary)
        create_chemical(db, name="Acid", hazard_class="corrosive", created_by=officer["id"])
        create_chemical(db, name="Acid 2", hazard_class="corrosive", created_by=officer["id"])
        create_chemical(db, name="Gas", hazard_class="compressed_gas", created_by=officer["id"])

        summary = hazard_summary(db)
        assert summary["corrosive"] == 2
        assert summary["compressed_gas"] == 1
        assert summary["total"] == 3

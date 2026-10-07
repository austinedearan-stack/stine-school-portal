"""Database-level protections installed by migrations (DATABASE.md "Append-only enforcement").

Layer 2 - triggers: UPDATE/DELETE (and TRUNCATE on PostgreSQL) on append-only tables raise an
error. On PostgreSQL the only escape hatch is a deliberate maintenance session: the connection
must set ``portal.audit_maintenance = 'on'`` AND be the table owner (the migration role, never the
runtime role). The test suite uses it so the test runner can flush tables.

Layer 3 - privileges: the runtime role (``DB_APP_ROLE``, default ``portal_app``) loses UPDATE,
DELETE and TRUNCATE on these tables, if that role exists and is not the migrating role.

On PostgreSQL ``seq`` is assigned by a BEFORE INSERT trigger from a dedicated sequence, so a
client can neither choose nor forge it.
"""

from __future__ import annotations

import os

from django.db import migrations

_PG_FUNCTIONS = """
CREATE OR REPLACE FUNCTION portal_forbid_mutation() RETURNS trigger AS $$
BEGIN
    IF current_setting('portal.audit_maintenance', true) = 'on'
       AND current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = TG_RELID) THEN
        IF TG_LEVEL = 'STATEMENT' THEN RETURN NULL; END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'table % is append-only: % refused', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION portal_assign_seq() RETURNS trigger AS $$
BEGIN
    NEW.seq := nextval(TG_ARGV[0]::regclass);
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""


def _app_role() -> str:
    return os.environ.get("DB_APP_ROLE", "portal_app")


def _install(table: str):
    def forwards(apps, schema_editor):
        conn = schema_editor.connection
        q = conn.ops.quote_name
        if conn.vendor == "postgresql":
            seq_name = f"{table}_seq_seq"
            schema_editor.execute(_PG_FUNCTIONS)
            schema_editor.execute(f"CREATE SEQUENCE IF NOT EXISTS {q(seq_name)} AS bigint")
            schema_editor.execute(
                f"CREATE TRIGGER {q(table + '_assign_seq')} BEFORE INSERT ON {q(table)} "
                f"FOR EACH ROW EXECUTE FUNCTION portal_assign_seq('{seq_name}')"
            )
            schema_editor.execute(
                f"CREATE TRIGGER {q(table + '_append_only')} BEFORE UPDATE OR DELETE ON {q(table)} "
                f"FOR EACH ROW EXECUTE FUNCTION portal_forbid_mutation()"
            )
            schema_editor.execute(
                f"CREATE TRIGGER {q(table + '_no_truncate')} BEFORE TRUNCATE ON {q(table)} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION portal_forbid_mutation()"
            )
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_user, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", [_app_role()])
                current_user, role_exists = cursor.fetchone()
            if role_exists and current_user != _app_role():
                schema_editor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {q(table)} FROM {q(_app_role())}")
                schema_editor.execute(f"GRANT USAGE, SELECT ON SEQUENCE {q(seq_name)} TO {q(_app_role())}")
        elif conn.vendor == "sqlite":
            for op in ("UPDATE", "DELETE"):
                schema_editor.execute(
                    f"CREATE TRIGGER {q(f'{table}_no_{op.lower()}')} BEFORE {op} ON {q(table)} "
                    f"BEGIN SELECT RAISE(ABORT, 'table {table} is append-only: {op} refused'); END"
                )

    def backwards(apps, schema_editor):
        conn = schema_editor.connection
        q = conn.ops.quote_name
        if conn.vendor == "postgresql":
            for trigger in ("_assign_seq", "_append_only", "_no_truncate"):
                schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(table + trigger)} ON {q(table)}")
            schema_editor.execute(f"DROP SEQUENCE IF EXISTS {q(table + '_seq_seq')}")
        elif conn.vendor == "sqlite":
            for op in ("update", "delete"):
                schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(f'{table}_no_{op}')}")

    return forwards, backwards


def append_only_table(table: str) -> migrations.RunPython:
    forwards, backwards = _install(table)
    return migrations.RunPython(forwards, backwards)


def postgres_only_sql(sql: str, reverse_sql: str) -> migrations.RunPython:
    """Run SQL only on PostgreSQL (e.g. exclusion constraints); a no-op on other engines."""

    def forwards(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql)

    def backwards(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(reverse_sql)

    return migrations.RunPython(forwards, backwards)

from django.db import migrations

from apps.core.db_guards import append_only_table


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        append_only_table("core_auditlog"),
        append_only_table("core_securityevent"),
        append_only_table("core_auditseal"),
    ]

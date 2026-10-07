"""Seed the capability catalog permissions and the default groups (ARCHITECTURE.md §5.2)."""

from django.db import migrations

from apps.core.capabilities import sync_capability_groups


def forwards(apps, schema_editor):
    sync_capability_groups(
        apps.get_model("auth", "Group"),
        apps.get_model("auth", "Permission"),
        apps.get_model("contenttypes", "ContentType"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_append_only_guards"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

"""Append-only request history + the default request categories (DATABASE.md §2.6)."""

from django.db import migrations

from apps.core.db_guards import append_only_table

# code, name, requires_approval, is_transfer, allows_attachments, max_attachments, default_priority
CATEGORIES = [
    ("ACADEMIC_QUERY", "Academic query", False, False, True, 3, "NORMAL"),
    ("UNIT_REGISTRATION_ISSUE", "Unit registration issue", False, False, True, 3, "HIGH"),
    ("TRANSFER", "Program / department / campus transfer", True, True, True, 5, "NORMAL"),
    ("HOSTEL_REQUEST", "Hostel request", False, False, True, 3, "NORMAL"),
    ("HOSTEL_TRANSFER", "Hostel room transfer", True, False, True, 3, "NORMAL"),
    ("FEE_QUERY", "Fee query", False, False, True, 3, "NORMAL"),
    ("STUDENT_ID", "Student ID card", False, False, True, 2, "LOW"),
    ("TRANSCRIPT", "Transcript request", True, False, False, 0, "NORMAL"),
    ("PROGRAM_CHANGE", "Program change", True, False, True, 3, "NORMAL"),
    ("LEAVE", "Leave of absence / deferment", True, False, True, 3, "NORMAL"),
    ("TECH_SUPPORT", "Technical support", False, False, True, 3, "NORMAL"),
    ("GENERAL", "General enquiry", False, False, True, 3, "LOW"),
    ("OTHER", "Other", False, False, True, 3, "LOW"),
]


def seed_categories(apps, schema_editor):
    category_model = apps.get_model("student_requests", "RequestCategory")
    for order, (code, name, approval, transfer, attachments, max_att, priority) in enumerate(CATEGORIES, start=1):
        category_model.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "requires_approval": approval,
                "is_transfer": transfer,
                "approval_capability": "approve_transfers" if transfer else "approve_requests",
                "allows_attachments": attachments,
                "max_attachments": max_att,
                "default_priority": priority,
                "sort_order": order * 10,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("student_requests", "0001_initial")]

    operations = [
        append_only_table("student_requests_requeststatuschange"),
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]

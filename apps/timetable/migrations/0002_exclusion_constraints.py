"""PostgreSQL exclusion constraints: no two entries in the same semester and day may overlap in
time for the same venue, the same lecturer or the same student group (DATABASE.md §2.3).

Service validation gives readable messages on every engine; these constraints make concurrent
clashing inserts impossible regardless of application locking. Requires btree_gist (trusted
extension; created by deploy/postgres/init or here when permitted).
"""

from django.db import migrations

from apps.core.db_guards import postgres_only_sql

TIME_RANGE = "tsrange(DATE '2000-01-01' + start_time, DATE '2000-01-01' + end_time)"

SQL = f"""
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE timetable_timetableentry ADD CONSTRAINT timetable_no_venue_overlap
    EXCLUDE USING gist (semester_id WITH =, day_of_week WITH =, venue_id WITH =, {TIME_RANGE} WITH &&);
ALTER TABLE timetable_timetableentry ADD CONSTRAINT timetable_no_lecturer_overlap
    EXCLUDE USING gist (semester_id WITH =, day_of_week WITH =, lecturer_id WITH =, {TIME_RANGE} WITH &&)
    WHERE (lecturer_id IS NOT NULL);
ALTER TABLE timetable_timetableentry ADD CONSTRAINT timetable_no_group_overlap
    EXCLUDE USING gist (semester_id WITH =, day_of_week WITH =, student_group_id WITH =, {TIME_RANGE} WITH &&)
    WHERE (student_group_id IS NOT NULL);
"""

REVERSE_SQL = """
ALTER TABLE timetable_timetableentry DROP CONSTRAINT IF EXISTS timetable_no_venue_overlap;
ALTER TABLE timetable_timetableentry DROP CONSTRAINT IF EXISTS timetable_no_lecturer_overlap;
ALTER TABLE timetable_timetableentry DROP CONSTRAINT IF EXISTS timetable_no_group_overlap;
"""


class Migration(migrations.Migration):
    dependencies = [("timetable", "0001_initial")]

    operations = [postgres_only_sql(SQL, REVERSE_SQL)]

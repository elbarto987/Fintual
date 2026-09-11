import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models

# Trigger function: recomputes search_vector from title + body on every
# INSERT, and on UPDATE only when title or body actually changed (avoids
# rewriting the tsvector on unrelated updates, e.g. view_count bumps).
CREATE_TRIGGER_FUNCTION = """
    CREATE FUNCTION post_search_vector_update() RETURNS trigger AS $$
    BEGIN
        NEW.search_vector := to_tsvector('english', coalesce(NEW.title, '') || ' ' || coalesce(NEW.body, ''));
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""
DROP_TRIGGER_FUNCTION = "DROP FUNCTION IF EXISTS post_search_vector_update();"

CREATE_TRIGGER = """
    CREATE TRIGGER post_search_vector_trigger
    BEFORE INSERT OR UPDATE OF title, body ON blog_post
    FOR EACH ROW EXECUTE FUNCTION post_search_vector_update();
"""
DROP_TRIGGER = "DROP TRIGGER IF EXISTS post_search_vector_trigger ON blog_post;"

# Backfill: populate search_vector for any rows that already exist
# (e.g. a DB seeded with `manage.py seed` before this migration ran).
# Uses a raw UPDATE rather than the ORM's SearchVectorField helper so it
# works identically whether this runs before or after seeding.
BACKFILL_SEARCH_VECTOR = """
    UPDATE blog_post
    SET search_vector = to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''));
"""
BACKFILL_REVERSE_NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(editable=False, null=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.CharField(db_index=True, max_length=255),
        ),
        # Trigger must exist before we backfill, so any row touched
        # concurrently during the backfill is still handled consistently.
        migrations.RunSQL(
            sql=CREATE_TRIGGER_FUNCTION,
            reverse_sql=DROP_TRIGGER_FUNCTION,
        ),
        migrations.RunSQL(
            sql=CREATE_TRIGGER,
            reverse_sql=DROP_TRIGGER,
        ),
        migrations.RunSQL(
            sql=BACKFILL_SEARCH_VECTOR,
            reverse_sql=BACKFILL_REVERSE_NOOP,
        ),
        migrations.AddIndex(
            model_name="post",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="post_search_vector_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="post",
            index=models.Index(
                fields=["is_published", "-created_at", "-id"],
                name="post_pub_created_idx",
            ),
        ),
    ]

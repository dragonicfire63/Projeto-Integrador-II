from django.db import migrations, models


def move_legacy_general_notes_to_unfiled(apps, schema_editor):
    Note = apps.get_model("anotacoes", "Note")
    Note.objects.using(schema_editor.connection.alias).filter(folder="geral").update(folder="")


class Migration(migrations.Migration):
    dependencies = [("anotacoes", "0003_import_existing_note_folders")]

    operations = [
        migrations.AlterField(
            model_name="note",
            name="folder",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.RunPython(move_legacy_general_notes_to_unfiled, migrations.RunPython.noop),
    ]
from django.db import migrations
from django.utils.text import slugify


def import_note_folders(apps, schema_editor):
    Folder = apps.get_model("anotacoes", "Folder")
    Note = apps.get_model("anotacoes", "Note")
    database = schema_editor.connection.alias

    existing = Note.objects.using(database).values_list("user_id", "folder").distinct()
    for user_id, old_folder in existing.iterator():
        name = old_folder.replace("-", " ").replace("_", " ").strip().title() or "Minhas notas"
        base_slug = slugify(old_folder)[:80] or "pasta"
        if Folder.objects.using(database).filter(user_id=user_id, slug=base_slug).exists():
            Note.objects.using(database).filter(user_id=user_id, folder=old_folder).update(folder=base_slug)
            continue
        slug = base_slug
        suffix = 2
        while Folder.objects.using(database).filter(user_id=user_id, slug=slug).exists():
            suffix_text = f"-{suffix}"
            slug = f"{base_slug[:80-len(suffix_text)]}{suffix_text}"
            suffix += 1
        Folder.objects.using(database).create(
            user_id=user_id,
            name=name,
            slug=slug,
            tag=name.upper()[:80],
        )
        Note.objects.using(database).filter(user_id=user_id, folder=old_folder).update(folder=slug)


class Migration(migrations.Migration):
    dependencies = [("anotacoes", "0002_folder")]

    operations = [migrations.RunPython(import_note_folders, migrations.RunPython.noop)]
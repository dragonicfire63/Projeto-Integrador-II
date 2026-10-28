from django.db import migrations, models


def clear_builtin_categories(apps, schema_editor):
    CalendarEvent = apps.get_model("anotacoes", "CalendarEvent")
    CalendarEvent.objects.filter(category__in=["pessoal", "trabalho", "amigos", "aniversarios"]).update(category="")


class Migration(migrations.Migration):

    dependencies = [
        ("anotacoes", "0005_alter_calendarevent_category_calendarcategory"),
    ]

    operations = [
        migrations.RunPython(clear_builtin_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="calendarevent",
            name="category",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
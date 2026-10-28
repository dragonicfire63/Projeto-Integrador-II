import json

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from anotacoes.google_drive import upload_backup
from anotacoes.models import GoogleDriveConnection, UserPreference
from anotacoes.views import _backup_data


FREQUENCY_DAYS = {"diaria": 1, "semanal": 7, "mensal": 30}


class Command(BaseCommand):
    help = "Executa backups vencidos dos usuários conectados ao Google Drive."

    def handle(self, *args, **options):
        completed = 0
        for connection in GoogleDriveConnection.objects.select_related("user"):
            preference, _ = UserPreference.objects.get_or_create(user=connection.user)
            values = preference.values if isinstance(preference.values, dict) else {}
            if not values.get("autoBackup", True):
                continue
            interval_days = FREQUENCY_DAYS.get(values.get("freq", "diaria"), 1)
            try:
                previous = parse_datetime(values.get("lastBackupAt", ""))
            except (TypeError, ValueError):
                previous = None
            if previous and (now() - previous).total_seconds() < interval_days * 86400:
                continue
            backup = _backup_data(connection.user)
            try:
                upload_backup(connection.user, json.dumps(backup, ensure_ascii=False))
            except Exception as exc:
                self.stderr.write(f"Falha no backup de {connection.user.username}: {exc}")
                continue
            values["lastBackupAt"] = backup["exportedAt"]
            preference.values = values
            preference.save(update_fields=["values"])
            completed += 1
            self.stdout.write(f"Backup concluído para {connection.user.username}")
        self.stdout.write(f"Total de backups executados: {completed}")

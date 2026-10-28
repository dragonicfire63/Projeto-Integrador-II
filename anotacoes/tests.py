import io
import json
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils.timezone import now
from PIL import Image as PillowImage

from anotacoes.management.commands.run_due_backups import Command
from anotacoes.models import CalendarCategory, CalendarEvent, Folder, GoogleDriveConnection, Note, PostIt, UserPreference


class BackendFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ana@example.com", email="ana@example.com", password="senha-forte-123")
        self.folder = Folder.objects.create(user=self.user, name="Estudos", slug="estudos", tag="ESTUDOS")

    def test_logout_ends_authenticated_session(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/")
        self.assertEqual(self.client.get("/notas.html").status_code, 302)

    def test_registration_creates_authenticated_account(self):
        response = self.client.post("/api/auth/register/", data=json.dumps({
            "name": "Beatriz", "email": "bia@example.com", "password": "senha-forte-123",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="bia@example.com")
        self.assertEqual(Folder.objects.filter(user=user).count(), 0)
        page = self.client.get("/notas.html")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["folders"], [])
        invalid_note = self.client.post("/api/notes/", data=json.dumps({
            "folder": "biologia", "title": "Nota sem pasta pessoal",
        }), content_type="application/json")
        self.assertEqual(invalid_note.status_code, 400)

    def test_account_can_be_deleted_by_its_owner(self):
        self.client.force_login(self.user)
        response = self.client.delete("/api/account/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/")
        self.assertFalse(User.objects.filter(id=self.user.id).exists())

    def test_user_can_upload_and_replace_profile_image(self):
        self.client.force_login(self.user)

        def image_file(name):
            image_data = io.BytesIO()
            PillowImage.new("RGB", (2, 2), color="red").save(image_data, format="PNG")
            return SimpleUploadedFile(name, image_data.getvalue(), content_type="image/png")

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            first_response = self.client.post(
                "/api/account/profile-image/", {"image": image_file("avatar.png")},
            )
            self.assertEqual(first_response.status_code, 200)
            preference = UserPreference.objects.get(user=self.user)
            previous_image = preference.profile_image.name
            self.assertTrue(preference.profile_image.storage.exists(previous_image))

            second_response = self.client.post(
                "/api/account/profile-image/", {"image": image_file("nova-foto.png")},
            )
            self.assertEqual(second_response.status_code, 200)
            preference.refresh_from_db()
            self.assertNotEqual(preference.profile_image.name, previous_image)
            self.assertFalse(preference.profile_image.storage.exists(previous_image))

    def test_backup_export_and_restore_only_affect_authenticated_user(self):
        self.client.force_login(self.user)
        child = Folder.objects.create(user=self.user, parent=self.folder, name="Subpasta", slug="subpasta")
        Note.objects.create(user=self.user, folder=child.slug, title="Nota guardada", content="conteúdo")
        other_user = User.objects.create_user(username="backup-alheio@example.com", password="senha-forte-123")
        Note.objects.create(user=other_user, title="Nota privada alheia")

        exported = self.client.get("/api/backup/")
        self.assertEqual(exported.status_code, 200)
        payload = exported.json()
        self.assertEqual(len(payload["folders"]), 2)
        self.assertEqual([item["title"] for item in payload["notes"]], ["Nota guardada"])

        Note.objects.filter(user=self.user).delete()
        Folder.objects.filter(user=self.user).delete()
        restored = self.client.post("/api/backup/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json(), {"ok": True, "folders": 2, "notes": 1})
        note = Note.objects.get(user=self.user)
        self.assertEqual(note.folder, "subpasta")
        self.assertTrue(Folder.objects.filter(user=self.user, slug="subpasta", parent__slug="estudos").exists())
        self.assertTrue(Note.objects.filter(user=other_user, title="Nota privada alheia").exists())

    def test_backup_restore_rejects_bad_folder_reference_without_deleting_data(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, title="Preservada")
        response = self.client.post("/api/backup/", data=json.dumps({
            "app": "Conexo", "formatVersion": 1, "folders": [],
            "notes": [{"title": "Inválida", "folder": "inexistente"}],
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Note.objects.filter(id=note.id).exists())

    def test_google_drive_status_reports_not_configured(self):
        self.client.force_login(self.user)
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get("/api/backup/google/status/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["configured"])
        self.assertFalse(response.json()["connected"])

    @patch("anotacoes.management.commands.run_due_backups.upload_backup")
    def test_due_backup_command_uses_saved_frequency(self, upload_mock):
        connection = GoogleDriveConnection.objects.create(
            user=self.user, encrypted_credentials="placeholder",
        )
        preference = UserPreference.objects.create(user=self.user, values={
            "autoBackup": True,
            "freq": "semanal",
            "lastBackupAt": (now() - timedelta(days=8)).isoformat(),
        })
        with patch("anotacoes.management.commands.run_due_backups._backup_data", return_value={
            "app": "Conexo", "formatVersion": 1, "exportedAt": now().isoformat(),
            "folders": [], "notes": [],
        }) as backup_mock:
            Command().handle()
        upload_mock.assert_called_once()
        backup_mock.assert_called_once_with(connection.user)
        preference.refresh_from_db()
        self.assertIn("lastBackupAt", preference.values)

    @patch("anotacoes.management.commands.run_due_backups.upload_backup")
    def test_due_backup_command_skips_when_frequency_not_reached(self, upload_mock):
        GoogleDriveConnection.objects.create(user=self.user, encrypted_credentials="placeholder")
        UserPreference.objects.create(user=self.user, values={
            "autoBackup": True,
            "freq": "mensal",
            "lastBackupAt": (now() - timedelta(days=2)).isoformat(),
        })
        Command().handle()
        upload_mock.assert_not_called()

    def test_note_and_postit_are_owned_by_authenticated_user(self):
        self.client.force_login(self.user)
        note_response = self.client.post("/api/notes/", data=json.dumps({
            "folder": self.folder.slug, "tag": self.folder.tag, "title": "Mitose", "content": "<p>Resumo</p>",
        }), content_type="application/json")
        self.assertEqual(note_response.status_code, 201)
        self.assertEqual(Note.objects.get().user, self.user)

        postit_response = self.client.post("/api/postits/", data=json.dumps({
            "title": "Revisar", "body": "Capítulo 1", "links": [],
        }), content_type="application/json")
        self.assertEqual(postit_response.status_code, 201)
        self.assertEqual(PostIt.objects.get().user, self.user)

        other_user = User.objects.create_user(username="outra@example.com", password="senha-forte-123")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get("/api/notes/").json()["notes"], [])
        self.assertEqual(self.client.get("/api/postits/").json()["postits"], [])

    def test_note_can_be_created_and_updated_without_a_folder(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/notes/", data=json.dumps({
            "title": "Rascunho", "content": "Nota independente",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        note = Note.objects.get()
        self.assertEqual(note.folder, "")

        page = self.client.get("/notas.html")
        self.assertEqual(page.context["unfiled_notes"][0]["id"], note.id)
        updated = self.client.patch(f"/api/notes/{note.id}/", data=json.dumps({
            "folder": "", "title": "Rascunho atualizado",
        }), content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.folder, "")
        self.assertEqual(note.title, "Rascunho atualizado")

    def test_note_can_move_between_folders_and_become_unfiled(self):
        self.client.force_login(self.user)
        other_folder = Folder.objects.create(user=self.user, name="Pessoal", slug="pessoal", tag="PESSOAL")
        note = Note.objects.create(user=self.user, folder="", title="Lembrete")

        first_move = self.client.patch(f"/api/notes/{note.id}/", data=json.dumps({
            "folder": self.folder.slug,
        }), content_type="application/json")
        self.assertEqual(first_move.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.folder, self.folder.slug)

        second_move = self.client.patch(f"/api/notes/{note.id}/", data=json.dumps({
            "folder": other_folder.slug,
        }), content_type="application/json")
        self.assertEqual(second_move.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.folder, other_folder.slug)

        remove_folder = self.client.patch(f"/api/notes/{note.id}/", data=json.dumps({
            "folder": "",
        }), content_type="application/json")
        self.assertEqual(remove_folder.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.folder, "")

    def test_note_cannot_move_to_another_users_folder(self):
        self.client.force_login(self.user)
        other_user = User.objects.create_user(username="dono@example.com", password="senha-forte-123")
        foreign_folder = Folder.objects.create(user=other_user, name="Privada", slug="privada")
        note = Note.objects.create(user=self.user, title="Minha nota")

        response = self.client.patch(f"/api/notes/{note.id}/", data=json.dumps({
            "folder": foreign_folder.slug,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        note.refresh_from_db()
        self.assertEqual(note.folder, "")

    def test_deleting_note_removes_only_the_owned_note(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Excluir-me")
        other_user = User.objects.create_user(username="dono2@example.com", password="senha-forte-123")
        other_note = Note.objects.create(user=other_user, title="Outra nota")

        response = self.client.delete(f"/api/notes/{note.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.filter(id=note.id).exists())
        self.assertTrue(Note.objects.filter(id=other_note.id).exists())

    def test_deleting_folder_keeps_its_notes_without_folder(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Nota preservada")
        child = Folder.objects.create(
            user=self.user, parent=self.folder, name="Subpasta", slug="subpasta",
        )
        child_note = Note.objects.create(user=self.user, folder=child.slug, title="Nota da subpasta")
        response = self.client.delete(f"/api/folders/{self.folder.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Folder.objects.filter(id=self.folder.id).exists())
        note.refresh_from_db()
        self.assertEqual(note.folder, "")
        child.refresh_from_db()
        child_note.refresh_from_db()
        self.assertIsNone(child.parent)
        self.assertEqual(child_note.folder, child.slug)

    def test_user_can_create_nested_folder_and_edit_folder_details(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/folders/", data=json.dumps({
            "name": "Projetos", "tag": "PROJ", "color_start": "#059669",
            "color_end": "#10B981", "parent": self.folder.slug,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        child_id = response.json()["folder"]["id"]
        child = Folder.objects.get(user=self.user, slug=child_id)
        self.assertEqual(child.parent, self.folder)

        note = Note.objects.create(user=self.user, folder=child.slug, title="Plano")
        updated = self.client.patch(f"/api/folders/{child.slug}/", data=json.dumps({
            "name": "Projetos pessoais", "tag": "PESSOAIS", "parent": self.folder.slug,
            "color_start": "#2563EB", "color_end": "#3B82F6",
        }), content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        child.refresh_from_db()
        note.refresh_from_db()
        self.assertEqual(child.name, "Projetos pessoais")
        self.assertEqual(child.slug, child_id)
        self.assertEqual(child.tag, "PESSOAIS")
        self.assertEqual(note.folder, child_id)

    def test_folder_tag_can_be_left_empty_when_created_and_edited(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/folders/", data=json.dumps({
            "name": "Sem tag",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        folder_id = response.json()["folder"]["id"]
        self.assertEqual(response.json()["folder"]["tag"], "")
        folder = Folder.objects.get(user=self.user, slug=folder_id)
        self.assertEqual(folder.tag, "")

        updated = self.client.patch(f"/api/folders/{folder_id}/", data=json.dumps({
            "tag": "",
        }), content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["folder"]["tag"], "")
        folder.refresh_from_db()
        self.assertEqual(folder.tag, "")

    def test_folder_cannot_be_moved_inside_its_descendant(self):
        self.client.force_login(self.user)
        child = Folder.objects.create(
            user=self.user, parent=self.folder, name="Filha", slug="filha",
        )
        grandchild = Folder.objects.create(
            user=self.user, parent=child, name="Neta", slug="neta",
        )
        response = self.client.patch(f"/api/folders/{self.folder.slug}/", data=json.dumps({
            "parent": grandchild.slug,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.folder.refresh_from_db()
        self.assertIsNone(self.folder.parent)

    def test_user_cannot_delete_another_users_folder(self):
        other_user = User.objects.create_user(username="dono3@example.com", password="senha-forte-123")
        foreign_folder = Folder.objects.create(user=other_user, name="Privada", slug="privada")
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/folders/{foreign_folder.slug}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Folder.objects.filter(id=foreign_folder.id).exists())

    def test_user_can_create_and_use_private_calendar_category(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/categories/", data=json.dumps({
            "name": "Estudos", "color": "#14B8A6",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        category = response.json()["category"]
        self.assertTrue(CalendarCategory.objects.filter(user=self.user, slug=category["id"]).exists())

        event_response = self.client.post("/api/events/", data=json.dumps({
            "title": "Revisão", "date": "2026-10-30", "time": "15:00", "cat": category["id"],
            "notif": {}, "note": None, "postit": False,
        }), content_type="application/json")
        self.assertEqual(event_response.status_code, 201)
        self.assertEqual(event_response.json()["event"]["cat"], category["id"])

        another_user = User.objects.create_user(username="categoria-outra@example.com", password="senha-forte-123")
        self.client.force_login(another_user)
        response = self.client.post("/api/events/", data=json.dumps({
            "title": "Outro", "date": "2026-10-30", "time": "16:00", "cat": category["id"],
            "notif": {}, "note": None, "postit": False,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_calendar_page_receives_account_categories(self):
        self.client.force_login(self.user)
        custom = CalendarCategory.objects.create(user=self.user, name="Saúde", slug="saude", color="#10B981")
        response = self.client.get("/calendario.html")
        category_ids = [category["id"] for category in response.context["categories_json"]]
        self.assertEqual(category_ids, [custom.slug])

    def test_calendar_starts_without_predefined_categories_and_accepts_uncategorized_event(self):
        self.client.force_login(self.user)
        page = self.client.get("/calendario.html")
        self.assertEqual(page.context["categories_json"], [])
        self.assertEqual(self.client.get("/api/categories/").json()["categories"], [])
        response = self.client.post("/api/events/", data=json.dumps({
            "title": "Lembrete", "date": "2026-10-30", "time": "15:00",
            "notif": {}, "note": None, "postit": False,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["event"]["cat"], "")

    def test_editor_shows_postits_linked_to_open_note(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Resumo")
        PostIt.objects.create(user=self.user, title="Revisar depois", links=[{
            "type": "note", "folderId": self.folder.slug, "title": note.title,
        }])
        PostIt.objects.create(user=self.user, title="Não vinculado", links=[])
        response = self.client.get(f"/editor_nota.html?id={note.id}")
        self.assertEqual([item["title"] for item in response.context["linked_postits"]], ["Revisar depois"])

    def test_custom_calendar_category_is_scoped_to_its_owner(self):
        self.client.force_login(self.user)
        category_response = self.client.post("/api/categories/", data=json.dumps({
            "name": "Saúde", "color": "#14B8A6",
        }), content_type="application/json")
        self.assertEqual(category_response.status_code, 201)
        category = category_response.json()["category"]
        calendar_page = self.client.get("/calendario.html")
        self.assertIn(category["id"], [item["id"] for item in calendar_page.context["categories_json"]])
        event_response = self.client.post("/api/events/", data=json.dumps({
            "title": "Consulta", "date": "2026-11-02", "time": "09:30",
            "cat": category["id"], "notif": {}, "note": None, "postit": False,
        }), content_type="application/json")
        self.assertEqual(event_response.status_code, 201)
        self.assertEqual(event_response.json()["event"]["cat"], category["id"])

        another_user = User.objects.create_user(username="calendario-outra@example.com", password="senha-forte-123")
        self.client.force_login(another_user)
        invalid_event = self.client.post("/api/events/", data=json.dumps({
            "title": "Evento", "date": "2026-11-02", "time": "10:00",
            "cat": category["id"], "notif": {}, "note": None, "postit": False,
        }), content_type="application/json")
        self.assertEqual(invalid_event.status_code, 400)

    def test_deleting_custom_category_reassigns_events_without_deleting_them(self):
        self.client.force_login(self.user)
        category = CalendarCategory.objects.create(
            user=self.user, name="Estudos", slug="estudos", color="#14B8A6",
        )
        event = CalendarEvent.objects.create(
            user=self.user, title="Revisão", date="2026-11-02", time="15:00", category=category.slug,
        )
        response = self.client.delete(f"/api/categories/{category.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events_reassigned"], 1)
        self.assertFalse(CalendarCategory.objects.filter(id=category.id).exists())
        event.refresh_from_db()
        self.assertEqual(event.category, "")

    def test_user_cannot_delete_missing_or_foreign_calendar_category(self):
        self.client.force_login(self.user)
        missing = self.client.delete("/api/categories/pessoal/")
        self.assertEqual(missing.status_code, 404)

        owner = User.objects.create_user(username="category-owner@example.com", password="senha-forte-123")
        foreign = CalendarCategory.objects.create(user=owner, name="Privada", slug="privada")
        response = self.client.delete(f"/api/categories/{foreign.slug}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(CalendarCategory.objects.filter(id=foreign.id).exists())

    def test_deleting_custom_category_preserves_events_in_personal_category(self):
        self.client.force_login(self.user)
        category = CalendarCategory.objects.create(
            user=self.user, name="Estudos", slug="estudos", color="#14B8A6",
        )
        event = CalendarEvent.objects.create(
            user=self.user, title="Revisão", date="2026-11-02", time="15:00", category=category.slug,
        )
        response = self.client.delete(f"/api/categories/{category.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events_reassigned"], 1)
        self.assertFalse(CalendarCategory.objects.filter(id=category.id).exists())
        event.refresh_from_db()
        self.assertEqual(event.category, "")

    def test_user_cannot_delete_another_users_category(self):
        owner = User.objects.create_user(username="cat-owner@example.com", password="senha-forte-123")
        category = CalendarCategory.objects.create(user=owner, name="Privada", slug="privada")
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/categories/{category.slug}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(CalendarCategory.objects.filter(id=category.id).exists())

    def test_editor_includes_calendar_postit_for_the_linked_note(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Resumo")
        event = CalendarEvent.objects.create(
            user=self.user,
            title="Revisar resumo",
            date="2026-11-02",
            time="09:30",
            note={"folder": self.folder.slug, "title": note.title},
        )
        postit = PostIt.objects.create(
            user=self.user, title=event.title, body="Revisão da nota", source="calendar", event_id=str(event.id),
        )
        manual_postit = PostIt.objects.create(
            user=self.user, title="Lembrete", body="Rascunho",
            links=[{"type": "note", "folderId": self.folder.slug, "title": note.title}],
        )
        response = self.client.get(f"/editor_nota.html?id={note.id}")
        self.assertEqual({item["id"] for item in response.context["linked_postits"]}, {postit.id, manual_postit.id})

        update_response = self.client.patch(f"/api/postits/{manual_postit.id}/", data=json.dumps({
            "title": "Revisão atualizada", "body": "Conteúdo atualizado",
        }), content_type="application/json")
        self.assertEqual(update_response.status_code, 200)
        manual_postit.refresh_from_db()
        self.assertEqual(manual_postit.title, "Revisão atualizada")
        self.assertEqual(manual_postit.body, "Conteúdo atualizado")
        self.assertEqual(manual_postit.links[0]["title"], "Resumo")

    def test_postit_created_from_note_editor_is_linked_to_that_note(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Resumo")
        response = self.client.post("/api/postits/", data=json.dumps({
            "title": "Lembrete da nota", "body": "Revisar este tópico",
            "links": [{"type": "note", "folderId": note.folder, "title": note.title}],
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        postit_id = response.json()["postit"]["id"]

        editor_response = self.client.get(f"/editor_nota.html?id={note.id}")
        self.assertEqual([item["id"] for item in editor_response.context["linked_postits"]], [postit_id])

    def test_manual_postit_can_be_deleted_from_note_editor(self):
        self.client.force_login(self.user)
        postit = PostIt.objects.create(
            user=self.user,
            title="Excluir daqui",
            links=[{"type": "note", "folderId": self.folder.slug, "title": "Resumo"}],
        )
        response = self.client.delete(f"/api/postits/{postit.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PostIt.objects.filter(id=postit.id).exists())

    def test_event_creates_and_removes_calendar_postit(self):
        self.client.force_login(self.user)
        note = Note.objects.create(user=self.user, folder=self.folder.slug, title="Resumo")
        response = self.client.post("/api/events/", data=json.dumps({
            "title": "Prova", "date": "2026-10-30", "time": "10:00",
            "desc": "Revisar", "notif": {"on": True, "before": 30, "sound": False},
            "note": {"folder": note.folder, "title": note.title}, "postit": True,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        event_id = response.json()["event"]["id"]
        self.assertTrue(CalendarEvent.objects.filter(id=event_id, user=self.user).exists())
        event = CalendarEvent.objects.get(id=event_id)
        self.assertEqual(event.notification, {"on": True, "before": 30, "sound": False})
        postit = PostIt.objects.get(user=self.user, event_id=str(event_id), source="calendar")
        self.assertEqual(postit.links, [{
            "type": "note", "folderId": note.folder, "title": note.title,
        }])

        disable_postit = self.client.patch(f"/api/events/{event_id}/", data=json.dumps({
            "title": "Prova", "date": "2026-10-30", "time": "10:00",
            "notif": {"on": False, "before": 5, "sound": True},
            "note": {"folder": note.folder, "title": note.title}, "postit": False,
        }), content_type="application/json")
        self.assertEqual(disable_postit.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.notification, {"on": False, "before": 5, "sound": True})
        self.assertFalse(PostIt.objects.filter(user=self.user, event_id=str(event_id), source="calendar").exists())

        enable_postit = self.client.patch(f"/api/events/{event_id}/", data=json.dumps({
            "title": "Prova", "date": "2026-10-30", "time": "10:00",
            "notif": {"on": True, "before": 10, "sound": True},
            "note": {"folder": note.folder, "title": note.title}, "postit": True,
        }), content_type="application/json")
        self.assertEqual(enable_postit.status_code, 200)
        self.assertTrue(PostIt.objects.filter(user=self.user, event_id=str(event_id), source="calendar").exists())

        delete_response = self.client.delete(f"/api/events/{event_id}/")
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(PostIt.objects.filter(event_id=str(event_id)).exists())

    def test_private_pages_redirect_to_login(self):
        response = self.client.get("/postits.html")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.url)

    def test_user_can_create_folder_and_add_notes_to_it(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/folders/", data=json.dumps({
            "name": "Projetos pessoais", "tag": "PROJETOS", "color_start": "#059669", "color_end": "#10B981",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        folder_data = response.json()["folder"]
        folder = Folder.objects.get(user=self.user, slug=folder_data["id"])

        note_response = self.client.post("/api/notes/", data=json.dumps({
            "folder": folder.slug, "tag": folder.tag, "title": "Ideias", "content": "Anotação",
        }), content_type="application/json")
        self.assertEqual(note_response.status_code, 201)
        self.assertEqual(Note.objects.get().folder, folder.slug)
        page_response = self.client.get("/notas.html")
        self.assertContains(page_response, "Projetos pessoais")

    def test_notes_page_includes_note_tags_for_tag_search(self):
        self.client.force_login(self.user)
        Note.objects.create(user=self.user, folder=self.folder.slug, title="Resumo", tag="FOTOSSINTESE")
        response = self.client.get("/notas.html")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["notes_json"][self.folder.slug][0]["tag"], "FOTOSSINTESE")

    def test_folder_names_are_unique_per_user_and_not_shared(self):
        self.client.force_login(self.user)
        create_url = "/api/folders/"
        payload = {"name": "Viagens"}
        first = self.client.post(create_url, data=json.dumps(payload), content_type="application/json")
        second = self.client.post(create_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["folder"]["id"], second.json()["folder"]["id"])

        other_user = User.objects.create_user(username="outra@example.com", password="senha-forte-123")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(create_url).json()["folders"], [])
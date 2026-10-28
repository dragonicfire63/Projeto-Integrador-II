import json
import re
from datetime import date, datetime, time

import bleach
from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.forms import ImageField
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie

from .google_drive import (
    download_backup,
    encrypt_credentials,
    google_oauth_configured,
    oauth_flow,
    storage_quota,
    upload_backup,
)
from .models import CalendarCategory, CalendarEvent, Folder, GoogleDriveConnection, Note, PostIt, UserPreference

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _json_body(request):
    try:
        return json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


@ensure_csrf_cookie
def home(request):
    return render(request, "index.html")


@login_required
def notes_page(request):
    folders = {}
    notes = Note.objects.filter(user=request.user)
    for note in notes.exclude(folder=""):
        folders.setdefault(note.folder, []).append({
            "id": note.id,
            "t": note.title,
            "e": note.content[:180],
            "p": note.content[:180],
            "tag": note.tag,
            "created_at": note.created_at.isoformat(),
            "updated_at": note.updated_at.isoformat(),
        })
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    return render(request, "notas.html", {
        "notes_json": folders,
        "unfiled_notes": [_note_data(note) for note in notes.filter(folder="")],
        "total_notes": notes.count(),
        "folders": [_folder_data(folder) for folder in Folder.objects.filter(user=request.user)],
        "preferences_json": preference.values,
    })


def _page_note_context(user):
    folders = {}
    notes = Note.objects.filter(user=user)
    for note in notes.exclude(folder=""):
        preview = bleach.clean(note.content, tags=[], strip=True)[:180]
        folders.setdefault(note.folder, []).append({
            "id": note.id, "t": note.title, "title": note.title,
            "e": preview, "p": preview, "tag": note.tag,
        })
    return {
        "notes_json": folders,
        "unfiled_notes_json": [_note_data(note) for note in notes.filter(folder="")],
        "folders_json": [_folder_data(folder) for folder in Folder.objects.filter(user=user)],
    }


@login_required
def editor_page(request):
    note = None
    folder = None
    note_id = request.GET.get("id")
    if note_id:
        note = get_object_or_404(Note, id=note_id, user=request.user)
        folder = Folder.objects.filter(user=request.user, slug=note.folder).first()
    if note:
        note.content = bleach.clean(note.content, tags=["p", "br", "b", "strong", "i", "em", "u", "h2", "h3", "ul", "ol", "li"], attributes={}, strip=True)
    linked_postits = []
    if note:
        note_ref = {"type": "note", "folderId": note.folder or None, "title": note.title}
        events_for_note = {
            str(event.id) for event in CalendarEvent.objects.filter(user=request.user, note__isnull=False)
            if event.note and event.note.get("folder") == (note.folder or None)
            and event.note.get("title") == note.title
        }
        for postit in PostIt.objects.filter(user=request.user):
            directly_linked = any(
                link.get("type") == note_ref["type"]
                and link.get("folderId") == note_ref["folderId"]
                and link.get("title") == note_ref["title"]
                for link in postit.links if isinstance(link, dict)
            )
            if directly_linked or (postit.source == "calendar" and postit.event_id in events_for_note):
                linked_postits.append({
                    "id": postit.id,
                    "title": postit.title,
                    "body": postit.body,
                    "source": postit.source,
                    "created_at": postit.created_at.isoformat(),
                })
    linked_postits.sort(key=lambda item: item["created_at"], reverse=True)
    return render(request, "editor_nota.html", {
        "note": note,
        "folder": folder,
        "folders_json": [_folder_data(item) for item in Folder.objects.filter(user=request.user)],
        "linked_postits": linked_postits,
    })


def page(template_name):
    @login_required
    def render_page(request):
        context = _page_note_context(request.user)
        if template_name == "calendario.html":
            context["events_json"] = [_event_data(event) for event in CalendarEvent.objects.filter(user=request.user)]
            context["categories_json"] = [
                _category_data(item) for item in CalendarCategory.objects.filter(user=request.user)
            ]
        return render(request, template_name, context)
    return render_page


@login_required
def settings_page(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    return render(request, "configuracoes.html", {
        "profile_name": request.user.first_name,
        "profile_email": request.user.email,
        "profile_image_url": preference.profile_image.url if preference.profile_image else "",
        "preferences_json": preference.values,
        "last_backup_at": preference.values.get("lastBackupAt", ""),
    })


@require_http_methods(["POST"])
def register(request):
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    if not name or not email:
        return _error("Informe nome e e-mail.")
    if User.objects.filter(username=email).exists():
        return _error("Este e-mail já está cadastrado.", 409)
    try:
        validate_password(password, User(username=email, email=email, first_name=name))
    except Exception as exc:
        return _error(" ".join(exc.messages))
    user = User.objects.create_user(username=email, email=email, password=password, first_name=name[:150])
    auth_login(request, user)
    return JsonResponse({"ok": True, "redirect": "/notas.html"}, status=201)


@require_http_methods(["POST"])
def login(request):
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    email = str(data.get("email", "")).strip().lower()
    user = authenticate(request, username=email, password=data.get("password", ""))
    if user is None:
        return _error("E-mail ou senha incorretos.", 401)
    auth_login(request, user)
    return JsonResponse({"ok": True, "redirect": "/notas.html"})


@require_http_methods(["POST"])
@login_required
def logout(request):
    auth_logout(request)
    return JsonResponse({"ok": True, "redirect": "/"})


def _note_data(note):
    return {
        "id": note.id,
        "folder": note.folder,
        "tag": note.tag,
        "title": note.title,
        "content": note.content,
        "color_start": note.color_start,
        "color_end": note.color_end,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


def _folder_data(folder):
    return {
        "id": folder.slug,
        "name": folder.name,
        "tag": folder.tag,
        "color_start": folder.color_start,
        "color_end": folder.color_end,
        "parent": folder.parent.slug if folder.parent_id else None,
        "children_count": folder.children.filter(user=folder.user).count(),
        "count": Note.objects.filter(user=folder.user, folder=folder.slug).count(),
    }


@login_required
@require_http_methods(["GET", "POST"])
def folders_api(request):
    if request.method == "GET":
        return JsonResponse({"folders": [_folder_data(folder) for folder in Folder.objects.filter(user=request.user)]})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    name = " ".join(str(data.get("name", "")).split())
    if not name or len(name) > 80:
        return _error("Informe um nome de pasta com até 80 caracteres.")
    color_start = str(data.get("color_start", "#4F46E5"))
    color_end = str(data.get("color_end", "#7C3AED"))
    if not HEX_COLOR.fullmatch(color_start) or not HEX_COLOR.fullmatch(color_end):
        return _error("As cores informadas são inválidas.")
    parent_slug = str(data.get("parent") or "").strip()
    parent = None
    if parent_slug:
        parent = Folder.objects.filter(user=request.user, slug=parent_slug).first()
        if not parent:
            return _error("A pasta superior selecionada não existe.")
    base_slug = slugify(name)[:80] or "pasta"
    slug = base_slug
    suffix = 2
    while Folder.objects.filter(user=request.user, slug=slug).exists():
        suffix_text = f"-{suffix}"
        slug = f"{base_slug[:80-len(suffix_text)]}{suffix_text}"
        suffix += 1
    folder = Folder.objects.create(
        user=request.user,
        name=name,
        slug=slug,
        tag=str(data.get("tag", "")).strip()[:80],
        color_start=color_start,
        color_end=color_end,
        parent=parent,
    )
    return JsonResponse({"folder": _folder_data(folder)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def folder_detail_api(request, slug):
    folder = get_object_or_404(Folder, user=request.user, slug=slug)
    if request.method == "PATCH":
        data = _json_body(request)
        if data is None:
            return _error("Dados inválidos.")
        name = " ".join(str(data.get("name", folder.name)).split())
        tag = str(data.get("tag", folder.tag)).strip()
        color_start = str(data.get("color_start", folder.color_start))
        color_end = str(data.get("color_end", folder.color_end))
        if not name or len(name) > 80:
            return _error("Informe um nome de pasta com até 80 caracteres.")
        if not HEX_COLOR.fullmatch(color_start) or not HEX_COLOR.fullmatch(color_end):
            return _error("As cores informadas são inválidas.")
        parent_slug = str(data.get("parent") or "").strip()
        parent = None
        if parent_slug:
            parent = Folder.objects.filter(user=request.user, slug=parent_slug).first()
            if not parent:
                return _error("A pasta superior selecionada não existe.")
            descendant_ids = set()
            pending = [folder]
            while pending:
                current = pending.pop()
                if current.pk in descendant_ids:
                    continue
                descendant_ids.add(current.pk)
                pending.extend(current.children.all())
            if parent.pk in descendant_ids:
                return _error("Uma pasta não pode ser movida para dentro dela mesma ou de uma subpasta.")
        with transaction.atomic():
            folder.name = name
            folder.tag = tag[:80]
            folder.color_start = color_start
            folder.color_end = color_end
            folder.parent = parent
            folder.save()
        return JsonResponse({"folder": _folder_data(folder)})
    with transaction.atomic():
        Note.objects.filter(user=request.user, folder=folder.slug).update(folder="")
        folder.children.update(parent=folder.parent)
        folder.delete()
    return JsonResponse({"ok": True, "notes_unfiled": True})


@login_required
@require_http_methods(["GET", "POST"])
def notes_api(request):
    if request.method == "GET":
        items = Note.objects.filter(user=request.user)
        folder = request.GET.get("folder")
        if folder:
            items = items.filter(folder=folder)
        query = request.GET.get("q")
        if query:
            items = items.filter(title__icontains=query) | items.filter(content__icontains=query) | items.filter(tag__icontains=query)
        return JsonResponse({"notes": [_note_data(note) for note in items.distinct()]})
    data = _json_body(request)
    if data is None or not str(data.get("title", "")).strip():
        return _error("O título da nota é obrigatório.")
    folder = str(data.get("folder") or "").strip()[:80]
    if folder and not Folder.objects.filter(user=request.user, slug=folder).exists():
        return _error("A pasta selecionada não existe.")
    note = Note.objects.create(
        user=request.user,
        folder=folder,
        tag=str(data.get("tag", ""))[:80],
        title=str(data["title"]).strip()[:180],
        content=str(data.get("content", "")),
        color_start=str(data.get("color_start", "#4F46E5"))[:7],
        color_end=str(data.get("color_end", "#7C3AED"))[:7],
    )
    return JsonResponse({"note": _note_data(note)}, status=201)


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def note_detail_api(request, note_id):
    note = get_object_or_404(Note, id=note_id, user=request.user)
    if request.method == "GET":
        return JsonResponse({"note": _note_data(note)})
    if request.method == "DELETE":
        note.delete()
        return JsonResponse({"ok": True})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    for field, limit in (("folder", 80), ("tag", 80), ("title", 180), ("color_start", 7), ("color_end", 7)):
        if field in data:
            value = str(data[field] or "").strip() if field == "folder" else str(data[field])
            if field == "folder" and value and not Folder.objects.filter(user=request.user, slug=value).exists():
                return _error("A pasta selecionada não existe.")
            setattr(note, field, value[:limit])
    if "content" in data:
        note.content = str(data["content"])
    if not note.title.strip():
        return _error("O título da nota é obrigatório.")
    note.save()
    return JsonResponse({"note": _note_data(note)})


def _postit_data(item):
    return {"id": item.id, "title": item.title, "body": item.body, "links": item.links,
            "eventId": item.event_id or None, "source": item.source,
            "createdAt": item.created_at.isoformat()}


@login_required
@require_http_methods(["GET", "POST"])
def postits_api(request):
    if request.method == "GET":
        return JsonResponse({"postits": [_postit_data(item) for item in PostIt.objects.filter(user=request.user)]})
    data = _json_body(request)
    if data is None or not str(data.get("title", "")).strip():
        return _error("O título do post-it é obrigatório.")
    item = PostIt.objects.create(user=request.user, title=str(data["title"]).strip()[:80],
        body=str(data.get("body", ""))[:400], links=data.get("links", []),
        event_id=str(data.get("eventId") or ""), source="manual")
    return JsonResponse({"postit": _postit_data(item)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def postit_detail_api(request, item_id):
    item = get_object_or_404(PostIt, id=item_id, user=request.user, source="manual")
    if request.method == "DELETE":
        item.delete()
        return JsonResponse({"ok": True})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    for key, attr, limit in (("title", "title", 80), ("body", "body", 400), ("links", "links", None), ("eventId", "event_id", 80)):
        if key in data:
            value = data[key] if limit is None else str(data[key] or "")[:limit]
            setattr(item, attr, value)
    if not item.title.strip():
        return _error("O título do post-it é obrigatório.")
    item.save()
    return JsonResponse({"postit": _postit_data(item)})


def _event_data(event):
    return {"id": event.id, "title": event.title, "date": event.date.isoformat(),
            "time": event.time.strftime("%H:%M"), "cat": event.category,
            "desc": event.description, "notif": _notification_data(event.notification), "note": event.note,
            "postit": event.create_postit}


def _notification_data(value):
    value = value if isinstance(value, dict) else {}
    try:
        before = int(value.get("before", 10))
    except (TypeError, ValueError):
        before = 10
    if before not in {5, 10, 30, 60, 1440}:
        before = 10
    return {
        "on": bool(value.get("on", True)),
        "before": before,
        "sound": bool(value.get("sound", True)),
    }


def _category_data(category):
    return {"id": category.slug, "label": category.name, "color": category.color, "custom": True}


@login_required
@require_http_methods(["GET", "POST"])
def categories_api(request):
    if request.method == "GET":
        items = [_category_data(item) for item in CalendarCategory.objects.filter(user=request.user)]
        return JsonResponse({"categories": items})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    name = " ".join(str(data.get("name", "")).split())
    color = str(data.get("color", "#0EA5E9"))
    if not name or len(name) > 60:
        return _error("Informe um nome de até 60 caracteres.")
    if not HEX_COLOR.fullmatch(color):
        return _error("A cor informada é inválida.")
    base_slug = slugify(name)[:80] or "categoria"
    slug = base_slug
    suffix = 2
    while CalendarCategory.objects.filter(user=request.user, slug=slug).exists():
        suffix_text = f"-{suffix}"
        slug = f"{base_slug[:80-len(suffix_text)]}{suffix_text}"
        suffix += 1
    category = CalendarCategory.objects.create(user=request.user, name=name, slug=slug, color=color)
    return JsonResponse({"category": _category_data(category)}, status=201)


@login_required
@require_http_methods(["DELETE"])
def category_detail_api(request, slug):
    category = get_object_or_404(CalendarCategory, user=request.user, slug=slug)
    with transaction.atomic():
        # Os eventos guardam o slug como texto, então é limpada a referência antes de remover a categoria.
        reassigned = CalendarEvent.objects.filter(user=request.user, category=category.slug).update(category="")
        category.delete()
    return JsonResponse({"ok": True, "events_reassigned": reassigned, "fallback_category": ""})


def _save_event(user, data, event=None):
    if not str(data.get("title", "")).strip():
        raise ValueError("O título do evento é obrigatório.")
    event = event or CalendarEvent(user=user)
    event.title = str(data["title"]).strip()[:80]
    event.date = date.fromisoformat(data["date"])
    event.time = time.fromisoformat(data["time"])
    event.category = str(data.get("cat", "")).strip()[:80]
    event.description = str(data.get("desc", ""))
    event.notification = _notification_data(data.get("notif"))
    event.note = data.get("note")
    event.create_postit = bool(data.get("postit", True))
    if event.category and not CalendarCategory.objects.filter(user=user, slug=event.category).exists():
        raise ValueError("Categoria de evento inválida.")
    event.save()
    if event.create_postit:
        links = []
        if isinstance(event.note, dict) and event.note.get("title"):
            links.append({
                "type": "note",
                "folderId": event.note.get("folder") or None,
                "title": str(event.note["title"]),
            })
        PostIt.objects.update_or_create(user=user, event_id=str(event.id), defaults={
            "title": event.title,
            "body": f"{event.date.isoformat()} às {event.time.strftime('%H:%M')}\n{event.description}"[:400],
            "links": links,
            "source": "calendar",
        })
    else:
        PostIt.objects.filter(user=user, event_id=str(event.id), source="calendar").delete()
    return event


@login_required
@require_http_methods(["GET", "POST"])
def events_api(request):
    if request.method == "GET":
        return JsonResponse({"events": [_event_data(event) for event in CalendarEvent.objects.filter(user=request.user)]})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    try:
        event = _save_event(request.user, data)
    except (ValueError, KeyError, TypeError):
        return _error("Confira título, data, hora e categoria do evento.")
    return JsonResponse({"event": _event_data(event)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def event_detail_api(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, user=request.user)
    if request.method == "DELETE":
        event.delete()
        PostIt.objects.filter(user=request.user, event_id=str(event_id), source="calendar").delete()
        return JsonResponse({"ok": True})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    try:
        event = _save_event(request.user, data, event)
    except (ValueError, KeyError, TypeError):
        return _error("Confira título, data, hora e categoria do evento.")
    return JsonResponse({"event": _event_data(event)})


@login_required
@require_http_methods(["GET", "PATCH"])
def preferences_api(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    if request.method == "GET":
        return JsonResponse({"preferences": preference.values})
    data = _json_body(request)
    if data is None or not isinstance(data, dict):
        return _error("Preferências inválidas.")
    preference.values = {**preference.values, **data}
    preference.save(update_fields=["values"])
    return JsonResponse({"preferences": preference.values})


def _backup_data(user):
    return {
        "app": "Conexo",
        "formatVersion": 1,
        "exportedAt": datetime.now().astimezone().isoformat(),
        "folders": [
            {
                "slug": folder.slug,
                "name": folder.name,
                "tag": folder.tag,
                "color_start": folder.color_start,
                "color_end": folder.color_end,
                "parent": folder.parent.slug if folder.parent_id else None,
            }
            for folder in Folder.objects.filter(user=user).select_related("parent")
        ],
        "notes": [_note_data(note) for note in Note.objects.filter(user=user)],
    }


def _restore_backup(user, data):
    if not isinstance(data, dict) or data.get("app") != "Conexo" or data.get("formatVersion") != 1:
        return _error("O arquivo não é um backup Conexo compatível.")
    folders = data.get("folders")
    notes = data.get("notes")
    if not isinstance(folders, list) or not isinstance(notes, list) or len(folders) > 5000 or len(notes) > 50000:
        return _error("O backup contém uma estrutura inválida ou grande demais.")

    normalized_folders = {}
    for item in folders:
        if not isinstance(item, dict):
            return _error("Há uma pasta inválida no backup.")
        name = " ".join(str(item.get("name", "")).split())
        slug = str(item.get("slug") or slugify(name)).strip()
        start = str(item.get("color_start", "#4F46E5"))
        end = str(item.get("color_end", "#7C3AED"))
        if not name or len(name) > 80 or not re.fullmatch(r"[-a-zA-Z0-9_]{1,80}", slug) or slug in normalized_folders:
            return _error("O backup contém nomes ou identificadores de pasta inválidos.")
        if not HEX_COLOR.fullmatch(start) or not HEX_COLOR.fullmatch(end):
            return _error("O backup contém cores de pasta inválidas.")
        normalized_folders[slug] = {
            "name": name, "tag": str(item.get("tag", ""))[:80],
            "color_start": start, "color_end": end,
            "parent": str(item["parent"]) if item.get("parent") else None,
        }

    for item in normalized_folders.values():
        if item["parent"] and item["parent"] not in normalized_folders:
            return _error("O backup referencia uma pasta superior inexistente.")

    normalized_notes = []
    for item in notes:
        if not isinstance(item, dict):
            return _error("Há uma anotação inválida no backup.")
        title = str(item.get("title", "")).strip()
        folder = str(item.get("folder") or "")
        if not title or len(title) > 180 or (folder and folder not in normalized_folders):
            return _error("O backup contém uma anotação inválida ou ligada a uma pasta inexistente.")
        start = str(item.get("color_start", "#4F46E5"))
        end = str(item.get("color_end", "#7C3AED"))
        if not HEX_COLOR.fullmatch(start) or not HEX_COLOR.fullmatch(end):
            return _error("O backup contém cores de anotação inválidas.")
        normalized_notes.append({
            "title": title, "folder": folder, "tag": str(item.get("tag", ""))[:80],
            "content": bleach.clean(
                str(item.get("content", "")),
                tags=["p", "br", "b", "strong", "i", "em", "u", "h2", "h3", "ul", "ol", "li"],
                attributes={},
                strip=True,
            ),
            "color_start": start, "color_end": end,
        })

    with transaction.atomic():
        Note.objects.filter(user=user).delete()
        Folder.objects.filter(user=user).delete()
        created = {}
        pending = dict(normalized_folders)
        while pending:
            # Cria pastas cujos pais já existem.
            ready = [slug for slug, item in pending.items() if not item["parent"] or item["parent"] in created]
            if not ready:
                raise ValueError("O backup contém uma hierarquia de pastas circular.")
            for slug in ready:
                item = pending.pop(slug)
                created[slug] = Folder.objects.create(
                    user=user, slug=slug, name=item["name"], tag=item["tag"],
                    color_start=item["color_start"], color_end=item["color_end"],
                    parent=created.get(item["parent"]),
                )
        Note.objects.bulk_create([Note(user=user, **item) for item in normalized_notes])
    return JsonResponse({"ok": True, "folders": len(normalized_folders), "notes": len(normalized_notes)})


@login_required
@require_http_methods(["GET", "POST"])
def backup_api(request):
    if request.method == "GET":
        preference, _ = UserPreference.objects.get_or_create(user=request.user)
        data = _backup_data(request.user)
        preference.values = {**preference.values, "lastBackupAt": data["exportedAt"]}
        preference.save(update_fields=["values"])
        return JsonResponse(data)
    if len(request.body) > 10 * 1024 * 1024:
        return _error("O arquivo de backup excede o limite de 10 MB.", 413)
    data = _json_body(request)
    try:
        return _restore_backup(request.user, data)
    except ValueError as exc:
        return _error(str(exc))


@login_required
@require_http_methods(["GET"])
def google_drive_status_api(request):
    connected = GoogleDriveConnection.objects.filter(user=request.user).exists()
    result = {"configured": google_oauth_configured(), "connected": connected}
    if connected:
        try:
            quota = storage_quota(request.user)
            used = int(quota.get("usage", 0))
            limit = int(quota["limit"]) if quota.get("limit") else None
            result["storage"] = {
                "used": used,
                "limit": limit,
                "free": max(0, limit - used) if limit is not None else None,
            }
        except Exception:
            result["storage_error"] = "Não foi possível consultar a quota do Google Drive."
    return JsonResponse(result)


@login_required
@require_http_methods(["GET"])
def google_drive_connect_api(request):
    if not google_oauth_configured():
        return _error("O Google Drive ainda não foi configurado pelo administrador.", 503)
    flow = oauth_flow()
    authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    request.session["google_drive_oauth_state"] = state
    return JsonResponse({"authorization_url": authorization_url})


@login_required
@require_http_methods(["GET"])
def google_drive_callback(request):
    expected_state = request.session.pop("google_drive_oauth_state", None)
    if not expected_state or request.GET.get("state") != expected_state or request.GET.get("error"):
        return redirect("/configuracoes.html?google=error")
    try:
        flow = oauth_flow(state=expected_state)
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        GoogleDriveConnection.objects.update_or_create(
            user=request.user,
            defaults={"encrypted_credentials": encrypt_credentials(flow.credentials.to_json())},
        )
    except Exception:
        return redirect("/configuracoes.html?google=error")
    return redirect("/configuracoes.html?google=connected")


@login_required
@require_http_methods(["POST"])
def google_drive_backup_api(request):
    if not GoogleDriveConnection.objects.filter(user=request.user).exists():
        return _error("Conecte uma conta Google Drive antes de fazer o backup.", 409)
    backup = _backup_data(request.user)
    upload_backup(request.user, json.dumps(backup, ensure_ascii=False))
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    preference.values = {**preference.values, "lastBackupAt": backup["exportedAt"]}
    preference.save(update_fields=["values"])
    return JsonResponse({"ok": True, "lastBackupAt": backup["exportedAt"]})


@login_required
@require_http_methods(["POST"])
def google_drive_restore_api(request):
    if not GoogleDriveConnection.objects.filter(user=request.user).exists():
        return _error("Conecte uma conta Google Drive antes de restaurar.", 409)
    backup = download_backup(request.user)
    if backup is None:
        return _error("Nenhum backup Conexo foi encontrado no Google Drive.", 404)
    try:
        return _restore_backup(request.user, json.loads(backup))
    except (json.JSONDecodeError, ValueError) as exc:
        return _error(str(exc) or "O arquivo do Google Drive não é um backup válido.")


@login_required
@require_http_methods(["POST"])
def profile_image_api(request):
    uploaded_image = request.FILES.get("image")
    if not uploaded_image:
        return _error("Selecione uma imagem para enviar.")
    if uploaded_image.size > 5 * 1024 * 1024:
        return _error("A imagem deve ter no máximo 5 MB.")
    try:
        image = ImageField().clean(uploaded_image)
    except Exception:
        return _error("O arquivo selecionado não é uma imagem válida.")

    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    previous_image = preference.profile_image
    preference.profile_image = image
    preference.save(update_fields=["profile_image"])
    if previous_image and previous_image.name != preference.profile_image.name:
        previous_image.storage.delete(previous_image.name)
    return JsonResponse({"ok": True, "image_url": preference.profile_image.url})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def account_api(request):
    if request.method == "DELETE":
        user = request.user
        auth_logout(request)
        user.delete()
        return JsonResponse({"ok": True, "redirect": "/"})
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    name = str(data.get("name", request.user.first_name)).strip()
    email = str(data.get("email", request.user.email)).strip().lower()
    if not name or not email or User.objects.exclude(id=request.user.id).filter(email__iexact=email).exists():
        return _error("Informe nome e um e-mail ainda não cadastrado.")
    request.user.first_name = name[:150]
    request.user.email = email
    request.user.username = email
    request.user.save(update_fields=["first_name", "email", "username"])
    return JsonResponse({"name": request.user.first_name, "email": request.user.email})


@login_required
@require_http_methods(["POST"])
def password_api(request):
    data = _json_body(request)
    if data is None:
        return _error("Dados inválidos.")
    current = str(data.get("current", ""))
    new_password = str(data.get("new", ""))
    if not request.user.check_password(current):
        return _error("A senha atual está incorreta.", 403)
    try:
        validate_password(new_password, request.user)
    except Exception as exc:
        return _error(" ".join(exc.messages))
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    auth_login(request, request.user)
    return JsonResponse({"ok": True})
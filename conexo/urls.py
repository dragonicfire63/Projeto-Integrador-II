from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from anotacoes import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("index.html", views.home, name="index"),
    path("notas.html", views.notes_page, name="notes"),
    path("editor_nota.html", views.editor_page, name="note_editor"),
    path("postits.html", views.page("postits.html"), name="postits"),
    path("calendario.html", views.page("calendario.html"), name="calendar"),
    path("configuracoes.html", views.settings_page, name="settings"),
    path("api/auth/register/", views.register, name="api_register"),
    path("api/auth/login/", views.login, name="api_login"),
    path("api/auth/logout/", views.logout, name="api_logout"),
    path("api/notes/", views.notes_api, name="api_notes"),
    path("api/folders/", views.folders_api, name="api_folders"),
    path("api/folders/<slug:slug>/", views.folder_detail_api, name="api_folder_detail"),
    path("api/notes/<int:note_id>/", views.note_detail_api, name="api_note_detail"),
    path("api/postits/", views.postits_api, name="api_postits"),
    path("api/postits/<int:item_id>/", views.postit_detail_api, name="api_postit_detail"),
    path("api/events/", views.events_api, name="api_events"),
    path("api/categories/", views.categories_api, name="api_categories"),
    path("api/categories/<slug:slug>/", views.category_detail_api, name="api_category_detail"),
    path("api/events/<int:event_id>/", views.event_detail_api, name="api_event_detail"),
    path("api/preferences/", views.preferences_api, name="api_preferences"),
    path("api/backup/", views.backup_api, name="api_backup"),
    path("api/backup/google/status/", views.google_drive_status_api, name="api_google_drive_status"),
    path("api/backup/google/connect/", views.google_drive_connect_api, name="api_google_drive_connect"),
    path("api/backup/google/callback/", views.google_drive_callback, name="api_google_drive_callback"),
    path("api/backup/google/backup/", views.google_drive_backup_api, name="api_google_drive_backup"),
    path("api/backup/google/restore/", views.google_drive_restore_api, name="api_google_drive_restore"),
    path("api/account/profile-image/", views.profile_image_api, name="api_profile_image"),
    path("api/account/", views.account_api, name="api_account"),
    path("api/account/password/", views.password_api, name="api_password"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
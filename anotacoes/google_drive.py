import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet
from django.conf import settings
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from .models import GoogleDriveConnection

SCOPES = [
    "https://www.googleapis.com/auth/drive.appdata",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
BACKUP_FILENAME = "conexo-backup.json"


def google_oauth_configured():
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def redirect_uri():
    return os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://127.0.0.1:8000/api/backup/google/callback/",
    )


def oauth_flow(state=None):
    client_config = {
        "web": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri()],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri()
    return flow


def _cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt_credentials(credentials_json):
    return _cipher().encrypt(credentials_json.encode()).decode()


def _credentials_for(user):
    connection = GoogleDriveConnection.objects.get(user=user)
    credentials_json = _cipher().decrypt(connection.encrypted_credentials.encode()).decode()
    credentials = Credentials.from_authorized_user_info(json.loads(credentials_json), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        connection.encrypted_credentials = encrypt_credentials(credentials.to_json())
        connection.save(update_fields=["encrypted_credentials"])
    return connection, credentials


def drive_service(user):
    _, credentials = _credentials_for(user)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def upload_backup(user, backup_json):
    connection, _ = _credentials_for(user)
    drive = drive_service(user)
    media = MediaInMemoryUpload(backup_json.encode(), mimetype="application/json", resumable=False)
    if connection.backup_file_id:
        try:
            drive.files().get(fileId=connection.backup_file_id, fields="id").execute()
            return drive.files().update(
                fileId=connection.backup_file_id,
                media_body=media,
                fields="id,name,modifiedTime,size",
            ).execute()
        except Exception as exc:
            if getattr(exc, "resp", None) is None or exc.resp.status != 404:
                raise
    result = drive.files().create(
        body={"name": BACKUP_FILENAME, "mimeType": "application/json", "parents": ["appDataFolder"]},
        media_body=media,
        fields="id,name,modifiedTime,size",
    ).execute()
    connection.backup_file_id = result["id"]
    connection.save(update_fields=["backup_file_id"])
    return result


def storage_quota(user):
    return drive_service(user).about().get(fields="storageQuota").execute().get("storageQuota", {})


def download_backup(user):
    connection, _ = _credentials_for(user)
    drive = drive_service(user)
    file_id = connection.backup_file_id
    if not file_id:
        result = drive.files().list(
            spaces="appDataFolder",
            q=f"name='{BACKUP_FILENAME}' and trashed=false",
            pageSize=1,
            fields="files(id)",
        ).execute()
        files = result.get("files", [])
        if not files:
            return None
        file_id = files[0]["id"]
        connection.backup_file_id = file_id
        connection.save(update_fields=["backup_file_id"])
    return drive.files().get_media(fileId=file_id).execute().decode()
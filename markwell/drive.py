"""Google Drive integration: OAuth, folder lookup, upload, and deletion checks.

``get_google_drive_service`` blocks on ``flow.run_local_server(port=0)`` when a
fresh OAuth grant is needed -- it opens a browser and waits for the redirect.
Only call it from a worker thread, never from the GUI thread.
"""

import os
from typing import Callable, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from . import config

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive.readonly']


def _noop(msg: str) -> None:
    pass


def get_google_drive_service(log: Callable[[str], None] = _noop):
    token_path = config.token_path()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_path = config.credentials_path()
            if not credentials_path.exists():
                log("[ERROR] 'credentials.json' not found. Create a project in Google Cloud "
                    "Console, enable the Drive API, download the OAuth 2.0 Client ID (Desktop "
                    "App) credentials, and save the file as 'credentials.json'.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def get_or_create_folder(service, folder_name: str, log: Callable[[str], None] = _noop) -> Optional[str]:
    try:
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = response.get('files', [])

        if files:
            log(f"[*] Found existing Google Drive folder '{folder_name}' (ID: {files[0]['id']})")
            return files[0]['id']
        else:
            log(f"[*] Creating new Google Drive folder '{folder_name}'...")
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            log(f"[*] Folder created successfully. (ID: {folder.get('id')})")
            return folder.get('id')
    except HttpError as error:
        log(f"[ERROR] Failed to search/create folder: {error}")
        return None


def upload_file(service, file_path, folder_id: str, log: Callable[[str], None] = _noop) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        file_name = os.path.basename(str(file_path))
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaFileUpload(str(file_path), mimetype='text/markdown', resumable=True)
        # Request webViewLink and id
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        return True, file.get('webViewLink'), file.get('id')
    except HttpError as error:
        log(f"[ERROR] Failed to upload {file_path}: {error}")
        return False, None, None


def check_file_deleted(service, file_id: str) -> Optional[bool]:
    """Check whether a Drive file is gone.

    Returns ``True`` if the file is trashed or returns 404 (confirmed gone),
    ``False`` if it still exists normally, or ``None`` if the check itself
    errored (e.g. a transient network failure). Callers must treat ``None``
    the same as "not deleted" and must NOT remove history records on it --
    only a confirmed ``True`` should trigger cleanup.
    """
    try:
        file = service.files().get(fileId=file_id, fields='trashed').execute()
        return bool(file.get('trashed', False))
    except HttpError as e:
        if e.resp.status == 404:
            return True
        return None
    except Exception:
        return None

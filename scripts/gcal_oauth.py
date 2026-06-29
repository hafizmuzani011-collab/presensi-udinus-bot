"""Google Calendar OAuth2 Setup Script.
Jalankan sekali untuk autorisasi akses Google Calendar.
Hasil: token.json tersimpan di root project.

Usage: python scripts/gcal_oauth.py
"""
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(ROOT, "credentials.json")
TOKEN_FILE = os.path.join(ROOT, "token.json")


def main():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            print("Token expired, refreshing...")
            creds.refresh(Request())
            _save_token(creds)
            print("Token refreshed!")
        elif creds and creds.valid:
            print("Token sudah valid. Tidak perlu setup ulang.")
            return
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            print(f"ERROR: {CREDENTIALS_FILE} tidak ditemukan!")
            print("Download dari Google Cloud Console → Credentials → OAuth2 → Download JSON")
            return

        print("Membuka browser untuk login Google...")
        print("Setelah login, berikan akses 'Google Calendar'.")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        _save_token(creds)
        print()
        print("Setup berhasil! token.json sudah tersimpan.")
        print("Sekarang kamu bisa menjalankan: python scripts/gcal_add_schedule.py")


def _save_token(creds):
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


if __name__ == "__main__":
    main()

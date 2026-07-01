import json
import time
import requests
from pathlib import Path

TOKENS_FILE = Path(__file__).parent / "tokens.json"

class PolarClient:
    """Handles authentication and API calls to Polar AccessLink v4."""

    BASE_URL = "https://www.polaraccesslink.com/v4"
    V3_URL   = "https://www.polaraccesslink.com/v3"
    AUTH_URL = "https://auth.polar.com/oauth/token"

    def __init__(self):
        self.tokens = self._load_tokens()
        # Refresh immediately on startup if token is expired or missing expiry
        if self._is_expired():
            self._refresh()

    def _load_tokens(self):
        with open(TOKENS_FILE) as f:
            return json.load(f)

    def _save_tokens(self):
        with open(TOKENS_FILE, "w") as f:
            json.dump(self.tokens, f, indent=2)

    def _is_expired(self):
        """True if access token is missing, expired, or expires within 60 seconds."""
        expires_at = self.tokens.get("expires_at", 0)
        return time.time() >= expires_at - 60

    def _refresh(self):
        print("  Refreshing access token...")
        response = requests.post(
            self.AUTH_URL,
            auth=(self.tokens["client_id"], self.tokens["client_secret"]),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"]
            }
        )
        response.raise_for_status()
        new = response.json()
        self.tokens["access_token"] = new["access_token"]
        if "refresh_token" in new:
            self.tokens["refresh_token"] = new["refresh_token"]
        # Save expiry timestamp so we can refresh proactively next time
        self.tokens["expires_at"] = time.time() + new.get("expires_in", 3600)
        self._save_tokens()
        print("  Token refreshed and saved.")

    def get(self, endpoint, params=None, base_url=None):
        """Make a GET request. Refreshes token proactively if needed.

        base_url overrides the default v4 base (e.g. PolarClient.V3_URL for
        the v3 exercises API, which serves per-exercise samples and routes).
        """
        headers = {
            "Authorization": f"Bearer {self.tokens['access_token']}",
            "Accept": "application/json"
        }
        url = f"{base_url or self.BASE_URL}/{endpoint}"
        response = requests.get(url, headers=headers, params=params)

        # Safety net: retry once if we still get 401
        if response.status_code == 401:
            self._refresh()
            headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
            response = requests.get(url, headers=headers, params=params)

        response.raise_for_status()

        if response.status_code == 204 or not response.text:
            return None
        return response.json()
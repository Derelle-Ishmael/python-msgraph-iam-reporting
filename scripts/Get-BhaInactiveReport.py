"""
Inactive account report for Brooklyn Health Analytics.
Authenticates to Microsoft Graph as an app registration (client credentials),
flags accounts with no sign-in in 90+ days or that have never signed in,
resolves each flagged user's manager, and exports a dated remediation CSV.

Author: Derelle Ishmael
Requires: requests (pip install requests)
Auth: app registration bha-inactive-report-script, application permissions
      User.Read.All + AuditLog.Read.All, secret via environment variable.
"""

import os
import csv
import sys
import requests
from datetime import datetime, timedelta, timezone

# Config comes from the environment, never from the code
TENANT_ID = os.environ.get("BHA_TENANT_ID")
CLIENT_ID = os.environ.get("BHA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("BHA_CLIENT_SECRET")

if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
    sys.exit("Missing environment variables. Set BHA_TENANT_ID, BHA_CLIENT_ID, BHA_CLIENT_SECRET.")

GRAPH = "https://graph.microsoft.com/v1.0"


def get_token():
    """Client credentials flow: POST to the token endpoint, receive an access token."""
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    body = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    response = requests.post(url, data=body)
    response.raise_for_status()
    return response.json()["access_token"]


def get_all_users(headers):
    """GET all users including sign-in activity, following pagination."""
    users = []
    url = f"{GRAPH}/users?$select=displayName,userPrincipalName,accountEnabled,signInActivity"
    while url:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        users.extend(data["value"])
        # Graph returns results in pages; @odata.nextLink is the next page, absent on the last
        url = data.get("@odata.nextLink")
    return users


def get_manager(headers, upn):
    """Resolve a user's manager. 404 means no manager assigned, which is a data point, not an error."""
    response = requests.get(f"{GRAPH}/users/{upn}/manager", headers=headers)
    if response.status_code == 404:
        return "(no manager assigned)"
    response.raise_for_status()
    return response.json().get("displayName", "(unknown)")


def main():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    threshold = datetime.now(timezone.utc) - timedelta(days=90)
    users = get_all_users(headers)
    flagged = []

    for user in users:
        activity = user.get("signInActivity") or {}
        last_signin_raw = activity.get("lastSignInDateTime")

        if last_signin_raw is None:
            flagged.append({
                "DisplayName": user["displayName"],
                "UPN": user["userPrincipalName"],
                "AccountEnabled": user["accountEnabled"],
                "LastSignIn": "Never",
                "DaysInactive": "N/A",
                "Manager": get_manager(headers, user["userPrincipalName"]),
                "Status": "FLAGGED - never signed in",
            })
        else:
            last_signin = datetime.fromisoformat(last_signin_raw.replace("Z", "+00:00"))
            if last_signin < threshold:
                flagged.append({
                    "DisplayName": user["displayName"],
                    "UPN": user["userPrincipalName"],
                    "AccountEnabled": user["accountEnabled"],
                    "LastSignIn": last_signin.strftime("%Y-%m-%d"),
                    "DaysInactive": (datetime.now(timezone.utc) - last_signin).days,
                    "Manager": get_manager(headers, user["userPrincipalName"]),
                    "Status": "FLAGGED - inactive 90+ days",
                })

    filename = f"../inactive_report_{datetime.now().strftime('%Y-%m-%d')}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=flagged[0].keys() if flagged else ["Status"])
        writer.writeheader()
        writer.writerows(flagged)

    print(f"Report complete. {len(flagged)} of {len(users)} accounts flagged. Exported to {filename}")


if __name__ == "__main__":
    main()
import os
import time
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================
# CONFIG / ENV VARS
# =========================

XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET")

if not XERO_CLIENT_ID or not XERO_CLIENT_SECRET:
    print("WARNING: XERO_CLIENT_ID or XERO_CLIENT_SECRET not set")

# =========================
# TEMP STORAGE (v1)
# =========================
# NOTE: This resets on redeploy. OK for now.
# firm_id -> { tenant_id, refresh_token }
FIRMS = {}

# firm_id -> { access_token, expires_at }
ACCESS_CACHE = {}

# =========================
# HELPERS
# =========================

def basic_auth_header():
    token = f"{XERO_CLIENT_ID}:{XERO_CLIENT_SECRET}"
    encoded = base64.b64encode(token.encode()).decode()
    return f"Basic {encoded}"

def refresh_access_token(firm_id: str) -> str:
    """
    Ensures a valid access token for the firm.
    Automatically refreshes and rotates refresh_token.
    """
    now = int(time.time())

    cached = ACCESS_CACHE.get(firm_id)
    if cached and cached["expires_at"] > now + 60:
        return cached["access_token"]

    firm = FIRMS.get(firm_id)
    if not firm:
        raise Exception("Firm not connected")

    resp = requests.post(
        "https://identity.xero.com/connect/token",
        headers={
            "Authorization": basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": firm["refresh_token"],
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"Token refresh failed: {resp.text}")

    data = resp.json()

    ACCESS_CACHE[firm_id] = {
        "access_token": data["access_token"],
        "expires_at": now + int(data.get("expires_in", 1800)),
    }

    # IMPORTANT: refresh tokens rotate
    firm["refresh_token"] = data["refresh_token"]

    return data["access_token"]

# =========================
# ROUTES
# =========================

@app.get("/")
def health():
    return "API OK", 200

@app.post("/firms/connect")
def firms_connect():
    """
    One-time bootstrap per firm.
    """
    payload = request.get_json(force=True) or {}

    firm_id = payload.get("firm_id")
    tenant_id = payload.get("tenant_id")
    refresh_token = payload.get("refresh_token")

    if not firm_id or not tenant_id or not refresh_token:
        return jsonify({
            "ok": False,
            "error": "firm_id, tenant_id, refresh_token required"
        }), 400

    FIRMS[firm_id] = {
        "tenant_id": tenant_id,
        "refresh_token": refresh_token,
    }

    ACCESS_CACHE.pop(firm_id, None)

    return jsonify({
        "ok": True,
        "firm_id": firm_id
    }), 200

@app.post("/clients/search")
def clients_search():
    payload = request.get_json(force=True) or {}

    firm_id = payload.get("firm_id")
    query = (payload.get("query") or "").strip()
    limit = int(payload.get("limit") or 5)

    if not firm_id or not query:
        return jsonify({
            "ok": False,
            "error": "firm_id and query required"
        }), 400

    firm = FIRMS.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "firm not connected"}), 400

    access_token = refresh_access_token(firm_id)

    where = f'Name.Contains("{query}")'

    resp = requests.get(
        "https://api.xero.com/api.xro/2.0/Contacts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": firm["tenant_id"],
            "Accept": "application/json",
        },
        params={"where": where},
        timeout=30,
    )

    if resp.status_code != 200:
        return jsonify({"ok": False, "error": resp.text}), resp.status_code

    contacts = resp.json().get("Contacts", [])[:limit]

    options = []
    for c in contacts:
        cid = c.get("ContactID")
        name = c.get("Name")
        email = c.get("EmailAddress") or ""
        if cid and name:
            label = f"{name} — {email}".strip(" —")
            options.append({"id": cid, "label": label})

    return jsonify({"ok": True, "options": options}), 200

@app.post("/clients/resolve")
def clients_resolve():
    payload = request.get_json(force=True) or {}

    firm_id = payload.get("firm_id")
    client_id = payload.get("client_id")

    if not firm_id or not client_id:
        return jsonify({
            "ok": False,
            "error": "firm_id and client_id required"
        }), 400

    firm = FIRMS.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "firm not connected"}), 400

    access_token = refresh_access_token(firm_id)

    resp = requests.get(
        f"https://api.xero.com/api.xro/2.0/Contacts/{client_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": firm["tenant_id"],
            "Accept": "application/json",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        return jsonify({"ok": False, "error": resp.text}), resp.status_code

    c = (resp.json().get("Contacts") or [{}])[0]

    address = (c.get("Addresses") or [{}])[0]

    return jsonify({
        "ok": True,
        "client_id": c.get("ContactID"),
        "full_name": c.get("Name"),
        "email": c.get("EmailAddress"),
        "phone": (c.get("Phones") or [{}])[0].get("PhoneNumber"),
        "address_line1": address.get("AddressLine1"),
        "city": address.get("City"),
        "state": address.get("Region"),
        "postcode": address.get("PostalCode"),
        "country": address.get("Country"),
    }), 200

# =========================
# ENTRYPOINT (local only)
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

import os
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "")

FIRMS = {}

ACCESS_CACHE = {}

def _basic_auth_header(client_id: str, client_secret: str) -> str:
    import base64
    token = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"

def refresh_access_token(firm_id: str) -> str:
    """Return a valid access_token for the firm. Refresh if needed. Persist rotated refresh_token."""
    now = int(time.time())

    cached = ACCESS_CACHE.get(firm_id)
    if cached and cached.get("expires_at", 0) - 60 > now:
        return cached["access_token"]

    firm = FIRMS.get(firm_id)
    if not firm or not firm.get("refresh_token"):
        raise ValueError("Firm not connected: missing refresh_token")

    refresh_token = firm["refresh_token"]

    resp = requests.post(
        "https://identity.xero.com/connect/token",
        headers={
            "Authorization": _basic_auth_header(XERO_CLIENT_ID, XERO_CLIENT_SECRET),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.status_code} {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    new_refresh_token = data["refresh_token"]
    expires_in = int(data.get("expires_in", 1800))

    # IMPORTANT: refresh tokens rotate; persist the new one
    FIRMS[firm_id]["refresh_token"] = new_refresh_token

    ACCESS_CACHE[firm_id] = {
        "access_token": access_token,
        "expires_at": now + expires_in,
    }
    return access_token

@app.get("/")
def home():
    return "API OK", 200

@app.post("/firms/connect")
def firms_connect():

    payload = request.get_json(force=True) or {}
    firm_id = payload.get("firm_id")
    tenant_id = payload.get("tenant_id")
    refresh_token = payload.get("refresh_token")
    if not firm_id or not tenant_id or not refresh_token:
        return jsonify({"ok": False, "error": "firm_id, tenant_id, refresh_token required"}), 400

    FIRMS[firm_id] = {"tenant_id": tenant_id, "refresh_token": refresh_token}
    return jsonify({"ok": True}), 200

    FIRMS[firm_id] = {"tenant_id": tenant_id, "refresh_token": refresh_token}
    return jsonify({"ok": True}), 200

@app.post("/clients/search")
def clients_search():
    payload = request.get_json(force=True) or {}
    firm_id = payload.get("firm_id")
    query = (payload.get("query") or "").strip()
    limit = int(payload.get("limit") or 5)

    if not firm_id or not query:
        return jsonify({"ok": False, "error": "firm_id and query required"}), 400

    firm = FIRMS.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "firm not connected"}), 400

    access_token = refresh_access_token(firm_id)
    tenant_id = firm["tenant_id"]

    # Xero 'where' filter; keep it simple to start
    where = f'Name.Contains("{query}")'

    resp = requests.get(
        "https://api.xero.com/api.xro/2.0/Contacts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
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
        name = c.get("Name") or ""
        email = c.get("EmailAddress") or ""
        label = f"{name} — {email}".strip(" —")
        if cid and name:
            options.append({"id": cid, "label": label})

    return jsonify({"ok": True, "options": options}), 200

@app.post("/clients/resolve")
def clients_resolve():
    payload = request.get_json(force=True) or {}
    firm_id = payload.get("firm_id")
    client_id = payload.get("client_id")

    if not firm_id or not client_id:
        return jsonify({"ok": False, "error": "firm_id and client_id required"}), 400

    firm = FIRMS.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "firm not connected"}), 400

    access_token = refresh_access_token(firm_id)
    tenant_id = firm["tenant_id"]

    resp = requests.get(
        f"https://api.xero.com/api.xro/2.0/Contacts/{client_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
            "Accept": "application/json",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return jsonify({"ok": False, "error": resp.text}), resp.status_code

    c = (resp.json().get("Contacts") or [{}])[0]

    # flatten the first address if present
    addr = (c.get("Addresses") or [])
    street = city = region = postcode = country = ""
    if addr:
        a0 = addr[0]
        street = a0.get("AddressLine1") or ""
        city = a0.get("City") or ""
        region = a0.get("Region") or ""
        postcode = a0.get("PostalCode") or ""
        country = a0.get("Country") or ""

    out = {
        "ok": True,
        "client_id": c.get("ContactID") or client_id,
        "full_name": c.get("Name") or "",
        "email": c.get("EmailAddress") or "",
        "phone": c.get("Phones", [{}])[0].get("PhoneNumber", "") if c.get("Phones") else "",
        "address_line1": street,
        "city": city,
        "state": region,
        "postcode": postcode,
        "country": country,
    }
    return jsonify(out), 200

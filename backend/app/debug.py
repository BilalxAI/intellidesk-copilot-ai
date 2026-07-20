import asyncio
import httpx
import jwt

TENANT_ID = ""
CLIENT_ID = ""
CLIENT_SECRET = ""

# =========================
# 1. APP-ONLY TOKEN CHECK
# =========================
async def check_app_permissions():
    print("\n🔵 APP-ONLY PERMISSION CHECK\n")

    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data)

    if response.status_code != 200:
        print("❌ App token failed:", response.text)
        return

    token = response.json()["access_token"]
    decoded = jwt.decode(token, options={"verify_signature": False})

    roles = decoded.get("roles", [])

    print("📌 APP ID:", decoded.get("appid"))
    print("📌 TENANT:", decoded.get("tid"))
    print("\n📌 APPLICATION PERMISSIONS (ROLES):")

    for r in roles:
        print("   ✔", r)

    print("\n📌 Admin Consent Check:")
    if len(roles) > 0:
        print("   ✅ Admin consent is LIKELY granted for app permissions")
    else:
        print("   ❌ No app permissions found (admin consent missing)")

    print("\n" + "="*50)


# =========================
# 2. DELEGATED TOKEN CHECK (DEVICE FLOW REQUIRED)
# =========================
async def check_delegated_permissions():
    print("\n🟢 DELEGATED PERMISSION CHECK\n")

    print("👉 This requires interactive login (device code flow).")
    print("👉 Run MSAL device flow to see delegated scopes.\n")

    print("Example scopes that would appear in delegated token:")
    print("   - Chat.ReadWrite")
    print("   - User.Read")
    print("   - offline_access")
    print("   - openid")
    print("   - profile")

    print("\n📌 Delegated permissions appear in: 'scp' claim (NOT roles)")
    print("="*50)


# =========================
# RUN BOTH CHECKS
# =========================
if __name__ == "__main__":
    asyncio.run(check_app_permissions())
    asyncio.run(check_delegated_permissions())
from msal import PublicClientApplication

CLIENT_ID = "f312cb9d-4244-41d6-8664-71f5a3d068c1"
TENANT_ID = "0a7fdb2d-3435-469b-9073-09fcbf780ddb"

app = PublicClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}"
)

flow = app.initiate_device_flow(scopes=[
    "User.Read",
    "Chat.ReadWrite"
])

print(flow["message"])

result = app.acquire_token_by_device_flow(flow)

print("ACCESS TOKEN:", "YES" if "access_token" in result else result)

    

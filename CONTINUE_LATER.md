# Continue Later Checklist

Current state is saved and runnable locally without any Azure subscription.

## Current Status

- Local Docker app is running and healthy on http://127.0.0.1:5055
- Health endpoint: http://127.0.0.1:5055/health
- Active container: mcb-app
- Helper scripts are ready:
  - .\scripts\start.ps1
  - .\scripts\stop.ps1

## Resume Commands

From project root:

```powershell
cd "c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds"
```

Start app:

```powershell
.\scripts\start.ps1
```

Check health quickly:

```powershell
& ".\.venv\Scripts\python.exe" -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5055/health', timeout=20).read().decode())"
```

Stop app:

```powershell
.\scripts\stop.ps1
```

## Next Work Options

1. Keep local-only workflow (no cloud costs):
   - Continue feature work and tests locally with Docker.

2. Return to Azure later (requires subscription access):
   - Sign in with an account that has a subscription.
   - Resume from Phase 3 provisioning.

## Notes

- Docker host port is 5055 (mapped to container port 5000).
- This avoids conflict with other local services that may use port 5000.

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
  Write-Error "Docker is not available in PATH. Start Docker Desktop and try again."
  exit 1
}

docker compose up --build -d
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Mathematically Correct Builds is running."
Write-Host "App:    http://127.0.0.1:5055"
Write-Host "Health: http://127.0.0.1:5055/health"
Write-Host ""
Write-Host "To stop: .\scripts\stop.ps1"

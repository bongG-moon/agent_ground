$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload

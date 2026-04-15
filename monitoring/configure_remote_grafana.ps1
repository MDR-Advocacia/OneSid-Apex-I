param(
    [string]$EnvPath = ".env"
)

$ErrorActionPreference = "Stop"

function Get-EnvMap {
    param([string]$Path)

    $map = @{}
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $key, $value = $line -split "=", 2
        $value = $value.Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $map[$key.Trim()] = $value
    }

    return $map
}

function Invoke-Grafana {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Headers,
        $Body = $null
    )

    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $Headers -ContentType "application/json"
    }

    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $Headers -Body $json -ContentType "application/json"
}

function Get-PythonExecutable {
    $candidates = @(
        (Join-Path (Get-Location) "venv\Scripts\python.exe"),
        (Join-Path (Get-Location) ".venv\Scripts\python.exe"),
        "python"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -eq "python") {
            return $candidate
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return "python"
}

$envMap = Get-EnvMap -Path $EnvPath

$grafanaUrl = ""
if ($envMap.ContainsKey("GRAFANA_REMOTE_URL")) {
    $grafanaUrl = $envMap["GRAFANA_REMOTE_URL"]
}
$grafanaUrl = $grafanaUrl.TrimEnd("/")

$grafanaUser = ""
if ($envMap.ContainsKey("GRAFANA_REMOTE_USER")) {
    $grafanaUser = $envMap["GRAFANA_REMOTE_USER"]
}

$grafanaPassword = ""
if ($envMap.ContainsKey("GRAFANA_REMOTE_PASSWORD")) {
    $grafanaPassword = $envMap["GRAFANA_REMOTE_PASSWORD"]
}

$lokiPublicUrl = ""
if ($envMap.ContainsKey("LOKI_PUBLIC_URL")) {
    $lokiPublicUrl = $envMap["LOKI_PUBLIC_URL"]
}
$lokiPublicUrl = $lokiPublicUrl.TrimEnd("/")

if (-not $grafanaUrl -or -not $grafanaUser -or -not $grafanaPassword -or -not $lokiPublicUrl) {
    throw "Preencha GRAFANA_REMOTE_URL, GRAFANA_REMOTE_USER, GRAFANA_REMOTE_PASSWORD e LOKI_PUBLIC_URL no .env."
}

$pair = "${grafanaUser}:${grafanaPassword}"
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{ Authorization = "Basic $auth" }

$datasourceUid = "onesid-loki"
$datasourceName = "Loki - OneSid Apex I"
$folderTitle = "OneSid"
$dashboardPath = Join-Path $PSScriptRoot "grafana\dashboards\onesid-observability.json"

$health = Invoke-Grafana -Method GET -Uri "$grafanaUrl/api/health" -Headers $headers
Write-Host "Grafana remoto OK:" $health.version

$datasources = Invoke-Grafana -Method GET -Uri "$grafanaUrl/api/datasources" -Headers $headers
$existingDatasource = $datasources | Where-Object { $_.uid -eq $datasourceUid } | Select-Object -First 1

$datasourceBody = @{
    uid       = $datasourceUid
    name      = $datasourceName
    type      = "loki"
    access    = "proxy"
    url       = $lokiPublicUrl
    isDefault = $false
    basicAuth = $false
    jsonData  = @{}
}

if ($existingDatasource) {
    Invoke-Grafana -Method PUT -Uri "$grafanaUrl/api/datasources/uid/$datasourceUid" -Headers $headers -Body $datasourceBody | Out-Null
    Write-Host "Datasource atualizada:" $datasourceName "->" $lokiPublicUrl
} else {
    Invoke-Grafana -Method POST -Uri "$grafanaUrl/api/datasources" -Headers $headers -Body $datasourceBody | Out-Null
    Write-Host "Datasource criada:" $datasourceName "->" $lokiPublicUrl
}

$folders = Invoke-Grafana -Method GET -Uri "$grafanaUrl/api/folders" -Headers $headers
$folder = $folders | Where-Object { $_.title -eq $folderTitle } | Select-Object -First 1

if (-not $folder) {
    $folder = Invoke-Grafana -Method POST -Uri "$grafanaUrl/api/folders" -Headers $headers -Body @{ title = $folderTitle }
    Write-Host "Pasta criada:" $folderTitle
} else {
    Write-Host "Pasta encontrada:" $folderTitle
}

$pythonExe = Get-PythonExecutable

$pythonScript = @"
import json
from pathlib import Path
import sys

import requests
from requests.auth import HTTPBasicAuth

grafana_url = sys.argv[1].rstrip("/")
grafana_user = sys.argv[2]
grafana_password = sys.argv[3]
folder_uid = sys.argv[4]
dashboard_path = Path(sys.argv[5])

dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
dashboard["id"] = None

payload = {
    "dashboard": dashboard,
    "folderUid": folder_uid,
    "overwrite": True,
    "message": "Atualizado pelo configure_remote_grafana.ps1",
}

response = requests.post(
    grafana_url + "/api/dashboards/db",
    auth=HTTPBasicAuth(grafana_user, grafana_password),
    json=payload,
    timeout=30,
)
response.raise_for_status()
print(json.dumps(response.json(), ensure_ascii=False))
"@

$pythonScript | & $pythonExe - $grafanaUrl $grafanaUser $grafanaPassword $folder.uid $dashboardPath | Out-Null
Write-Host "Dashboard publicado: OneSid Observabilidade"

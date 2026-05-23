from __future__ import annotations
content = (
    "Set-Location 'C:\\Users\\rendy\\"
    + "\u539f\u521d\u6620\u50cf\u7247\u5eab"
    + "'\r\n"
    "$LogDir = 'logs'\r\n"
    "if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }\r\n"
    "$TS = Get-Date -Format 'yyyyMMdd'\r\n"
    "$Log = Join-Path $LogDir \"sync_$TS.log\"\r\n"
    "\r\n"
    "function Run-Step($name, $script) {\r\n"
    "  \"=== [$name] $(Get-Date) ===\" | Out-File -FilePath $Log -Append -Encoding utf8\r\n"
    "  python -u $script *>> $Log\r\n"
    "  if ($LASTEXITCODE -ne 0) {\r\n"
    "    \"=== [FAILED at $name] $(Get-Date) ===\" | Out-File -FilePath $Log -Append -Encoding utf8\r\n"
    "    Write-Host \"FAILED at $name, see $Log\" -ForegroundColor Red\r\n"
    "    exit 1\r\n"
    "  }\r\n"
    "}\r\n"
    "\r\n"
    "Run-Step 'scan drive' 'scan.py'\r\n"
    "Run-Step 'scan nas' 'scan_nas.py'\r\n"
    "Run-Step 'refine' 'refine.py'\r\n"
    "Run-Step 'upload' 'upload.py'\r\n"
    "\r\n"
    "\"=== [done] $(Get-Date) ===\" | Out-File -FilePath $Log -Append -Encoding utf8\r\n"
    "Write-Host 'done' -ForegroundColor Green\r\n"
)
with open(r'/volume2/docker-prod/scripts/原初映像片庫/daily_sync.ps1', 'wb') as f:
    f.write(b'\xef\xbb\xbf')
    f.write(content.encode('utf-8'))
print('ok')

from __future__ import annotations
content = (
    "$name = '"
    + "\u539f\u521d\u6620\u50cf\u7247\u5eab_\u6bcf\u65e5\u540c\u6b65"
    + "'\r\n"
    "$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
    "-Argument '-NoProfile -ExecutionPolicy Bypass -File \"C:\\Users\\rendy\\"
    + "\u539f\u521d\u6620\u50cf\u7247\u5eab"
    + "\\daily_sync.ps1\"'\r\n"
    "Set-ScheduledTask -TaskName $name -Action $action\r\n"
    "Write-Host 'action updated' -ForegroundColor Green\r\n"
    "Get-ScheduledTask -TaskName $name | Select-Object -ExpandProperty Actions\r\n"
    "Read-Host 'Press Enter'\r\n"
)
with open(r'/volume2/docker-prod/scripts/原初映像片庫/_update_task_action.ps1', 'wb') as f:
    f.write(b'\xef\xbb\xbf')
    f.write(content.encode('utf-8'))
print('ok')

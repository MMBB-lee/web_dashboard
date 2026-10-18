"""Generate reviewable native schedules. No task is installed by generation."""
import json
from pathlib import Path
import plistlib
import sys

from .common import CrawlerError, write_json


def generate(data_root, output, python=None):
    root, output = Path(data_root).resolve(), Path(output).resolve()
    executable = Path(python or sys.executable).absolute()
    if output.exists() and any(output.iterdir()):
        raise CrawlerError("Use a new/empty schedule directory")
    output.mkdir(parents=True, exist_ok=True)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    label = "org.anhui.public-data-crawler"
    arguments = [str(executable), "-I", "-m", "anhui_crawler", "--data-root", str(root), "update", "--due"]
    job = {"Label": label, "ProgramArguments": arguments, "WorkingDirectory": str(root),
           "StartCalendarInterval": {"Hour": 9, "Minute": 0}, "RunAtLoad": False,
           "StandardOutPath": str(logs / "scheduler.stdout.log"), "StandardErrorPath": str(logs / "scheduler.stderr.log"),
           "EnvironmentVariables": {"PYTHONUTF8": "1", "TZ": "Asia/Shanghai"}}
    (output / "crawler.plist").write_bytes(plistlib.dumps(job))
    # Paths are parameters in Windows, so a Mac-generated package does not embed
    # a Mac interpreter or quote data paths into PowerShell command text.
    powershell = r'''param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$DataRoot,
  [switch]$Remove,
  [switch]$RunNow
)
$ErrorActionPreference = "Stop"
$taskName = "AnhuiPublicDataCrawler"
if ($Remove) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; exit 0 }
if ((Get-TimeZone).Id -ne "China Standard Time") { throw "This 09:00 schedule requires China Standard Time; no system timezone is changed." }
$Python = (Resolve-Path -LiteralPath $Python).Path
$DataRoot = (Resolve-Path -LiteralPath $DataRoot).Path
if ($Python.Contains('"') -or $DataRoot.Contains('"')) { throw "Unsupported quote in path" }
$arguments = '-I -m anhui_crawler --data-root "' + $DataRoot + '" update --due'
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $DataRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null
if ($RunNow) { Start-ScheduledTask -TaskName $taskName }
Get-ScheduledTaskInfo -TaskName $taskName | Format-List
'''
    (output / "Install-WindowsSchedule.ps1").write_text(powershell, encoding="utf-8-sig")
    (output / "README.md").write_text(
        "# 定时运行\n\n默认每天北京时间09:00执行update --due，按来源到期时间决定是否请求。\n\n"
        "Mac须确认系统时区Asia/Shanghai；plist的TZ只控制子进程，不改变launchd触发时区。"
        "使用launchctl bootstrap gui/$(id -u) /绝对路径/crawler.plist安装，"
        "launchctl kickstart gui/$(id -u)/org.anhui.public-data-crawler触发，"
        "launchctl bootout gui/$(id -u)/org.anhui.public-data-crawler移除。\n\n"
        "Windows使用Install-WindowsSchedule.ps1 -Python <解释器绝对路径> -DataRoot <目录>，"
        "用-RunNow立即触发，用-Remove移除。需要当前用户登录；不保存账户密码。\n\n"
        "两端仅启用一台日常主机。MySQL凭据通过运行账户环境配置，不放入任务或日志；"
        "环境变更后需重新加载任务。SQLite锁按库文件定位，MySQL锁按库名定位。\n",
        encoding="utf-8")
    result = {"status": "generated", "label": label, "schedule": "09:00 Asia/Shanghai", "installed": False,
              "mac_python": str(executable), "mac_data_root": str(root), "windows_paths_are_parameters": True}
    write_json(output / "manifest.json", result)
    return result

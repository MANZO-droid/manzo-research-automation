# 당일/주간 상승률 상위 자동 수집 - Windows 작업 스케줄러 등록 스크립트
#
# 참고: 지금은 GitHub Actions(.github/workflows/gainers-daily.yml)가 매일
# 자동으로 이 역할을 대신하고 있어, 이 스크립트는 보통 필요 없습니다.
# 로컬 PC에서도 이중으로 돌리면 같은 데이터를 두 번 쓰게 되니, GitHub Actions를
# 쓰고 있다면 이 스케줄러는 등록하지 마세요.
#
# PowerShell을 관리자 권한으로 실행 후 이 파일을 실행하세요:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\setup_scheduler.ps1

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath  = (Get-Command python).Source
$Script      = Join-Path $ProjectRoot "scripts\collect_gainers.py"

# ── 평일 오후 4시: 당일 리포트 ──────────────────────────────────────────────
$ActionDaily = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$Script`" --mode daily" `
    -WorkingDirectory $ProjectRoot

$TriggerDaily = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "16:00"

Register-ScheduledTask `
    -TaskName "ManzoGainers_Daily" `
    -Action $ActionDaily `
    -Trigger $TriggerDaily `
    -Description "평일 장 마감 후 상승률 상위 10위 자동 수집" `
    -RunLevel Highest `
    -Force

Write-Host "[등록 완료] ManzoGainers_Daily — 평일 오후 4:00" -ForegroundColor Green

# ── 토요일 오후 4시: 주간 리포트 ────────────────────────────────────────────
$ActionWeekly = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$Script`" --mode weekly" `
    -WorkingDirectory $ProjectRoot

$TriggerWeekly = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Saturday `
    -At "16:00"

Register-ScheduledTask `
    -TaskName "ManzoGainers_Weekly" `
    -Action $ActionWeekly `
    -Trigger $TriggerWeekly `
    -Description "토요일 주간 상승률 상위 10위 자동 수집" `
    -RunLevel Highest `
    -Force

Write-Host "[등록 완료] ManzoGainers_Weekly — 토요일 오후 4:00" -ForegroundColor Green

Write-Host ""
Write-Host "등록된 작업 확인:" -ForegroundColor Cyan
Get-ScheduledTask -TaskName "ManzoGainers_*" | Select-Object TaskName, State

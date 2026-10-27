#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Устанавливает Windows-службу ergo-ollama для модуля ollama_framework.
#>

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ModuleRoot = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$ProjectRoot = (Resolve-Path (Join-Path $ModuleRoot '..\..')).Path

$coreDeployment = Join-Path $ProjectRoot 'core\deployment\windows\lib'
. (Join-Path $coreDeployment 'core.ps1')
. (Join-Path $coreDeployment 'nssm.ps1')
. (Join-Path $coreDeployment 'services.ps1')

function New-OllamaServiceWrapper {
    param([string]$Root)

    $wrapperDir = Get-ProjectWrappersDir -ProjectRoot $Root
    $wrapperPath = Join-Path $wrapperDir 'start_ollama.bat'

    New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null

    $ergomsCmd = Join-Path $Root 'core\deployment\bin\ergoms.cmd'
    if (-not (Test-Path -LiteralPath $ergomsCmd)) {
        throw "Не найден $ergomsCmd"
    }

    $content = @(
        '@echo off',
        'chcp 65001 >nul',
        'set PYTHONIOENCODING=utf-8',
        'set PYTHONUTF8=1',
        "set `"PYTHONPATH=$Root`"",
        "cd /d `"$Root`"",
        "call `"$ergomsCmd`" ollama_framework:start-ollama"
    ) -join "`r`n"

    [System.IO.File]::WriteAllText(
        $wrapperPath, $content + "`r`n", (New-Object System.Text.UTF8Encoding($false))
    )
    return $wrapperPath
}

function Invoke-OllamaNssm {
    param([string]$Executable, [string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Executable @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($code -ne 0) {
        throw "NSSM $($Arguments[0]) failed (exit $code): $($output -join ' ')"
    }
    return $output
}

function Install-OllamaService {
    param([string]$Root)

    $serviceName = 'ergo-ollama'
    $nssmExe = Install-NSSM -Root $Root
    $existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($existingService) {
        $registered = ((Invoke-OllamaNssm $nssmExe @('get', $serviceName, 'AppDirectory')) -join "`n").Trim()
        $expected = [System.IO.Path]::GetFullPath((Join-Path $Root 'core')).TrimEnd([char[]]'\/')
        if (-not $registered -or [System.IO.Path]::GetFullPath($registered).TrimEnd([char[]]'\/') -ine $expected) {
            throw "Service $serviceName belongs to another project: $registered"
        }
    }

    # Validate and prepare the wrapper before stopping or removing an existing service.
    $wrapperPath = New-OllamaServiceWrapper -Root $Root
    $logsDir = Get-ProjectLogsDir -ProjectRoot $Root
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

    if ($existingService) {
        Write-ColorOutput "-> Service $serviceName already exists, reinstalling..." Yellow
        if ($existingService.Status -notin @('Stopped', 'StopPending')) {
            Invoke-OllamaNssm $nssmExe @('stop', $serviceName) | Out-Null
        }
        if (-not (Wait-ServiceStopped -ServiceName $serviceName -TimeoutSeconds 30)) {
            throw "Service $serviceName did not stop within 30 seconds"
        }
        Invoke-OllamaNssm $nssmExe @('remove', $serviceName, 'confirm') | Out-Null
        $deadline = (Get-Date).AddSeconds(30)
        while (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
            if ((Get-Date) -ge $deadline) {
                throw "Service $serviceName was not removed within 30 seconds"
            }
            Start-Sleep -Milliseconds 500
        }
    }

    Write-ColorOutput "-> Installing service: $serviceName" Cyan

    Invoke-OllamaNssm $nssmExe @('install', $serviceName, $wrapperPath) | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'DisplayName', "Ergo MS - $serviceName") | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'Description', 'Ergo Management System - Ollama Server') | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppDirectory', (Join-Path $Root 'core')) | Out-Null
    $singleLog = Join-Path $logsDir 'ollama-serve.log'
    $pythonExe = Join-Path $Root 'virtual_env\python\Scripts\python.exe'
    $logEnvScript = Join-Path $Root 'core\deployment\scripts\log_env.py'
    if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $logEnvScript)) {
        $resolved = & $pythonExe $logEnvScript path OLLAMA $Root 2>$null
        if (-not [string]::IsNullOrWhiteSpace($resolved)) {
            $singleLog = $resolved.Trim()
        }
    }
    $logParent = Split-Path -Parent $singleLog
    if ($logParent) {
        New-Item -ItemType Directory -Path $logParent -Force | Out-Null
    }
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppStdout', $singleLog) | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppStderr', $singleLog) | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppEnvironmentExtra', 'PYTHONIOENCODING=UTF-8', 'PYTHONUTF8=1', 'PYTHONUNBUFFERED=1') | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'Start', 'SERVICE_AUTO_START') | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppExit', 'Default', 'Restart') | Out-Null
    Invoke-OllamaNssm $nssmExe @('set', $serviceName, 'AppRestartDelay', '5000') | Out-Null

    Write-ColorOutput "[OK] Service $serviceName installed" Green
}

Write-ColorOutput "-> Installing Ollama service for: $ProjectRoot" Cyan
Install-OllamaService -Root $ProjectRoot
Start-Service -Name 'ergo-ollama'
Write-ColorOutput "`n[OK] Ollama service installed and started!" Green
Show-ServicesStatus -ProjectRoot $ProjectRoot

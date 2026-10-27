param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Scenario
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:calls = New-Object 'System.Collections.Generic.List[string]'
$script:messages = New-Object 'System.Collections.Generic.List[string]'
$script:clock = [datetime]'2026-01-01T00:00:00'
$script:present = $Scenario -in @('running', 'stopped', 'stop-pending', 'stop-timeout', 'remove-timeout', 'stop-error', 'remove-error', 'other-root', 'missing-cli')
$script:status = if ($Scenario -eq 'stopped') { 'Stopped' } elseif ($Scenario -eq 'stop-pending') { 'StopPending' } else { 'Running' }
$script:stopAt = if ($Scenario -eq 'stop-pending') { $script:clock.AddSeconds(3) } else { $null }
$script:removeAt = $null
$script:removing = $false
$script:installed = $false
$script:started = $false
$script:sleeps = 0
$script:cli = Join-Path $Root 'core\deployment\bin\ergoms.cmd'
New-Item -ItemType Directory -Path (Split-Path -Parent $script:cli) -Force | Out-Null
if ($Scenario -ne 'missing-cli') { Set-Content -LiteralPath $script:cli -Value '@exit /b 0' -Encoding ASCII }

function Update-FakeService {
    if ($null -ne $script:stopAt -and $script:clock -ge $script:stopAt) {
        $script:status = 'Stopped'
    }
    if ($script:removing -and $null -ne $script:removeAt -and $script:clock -ge $script:removeAt) {
        $script:present = $false
    }
}

function Get-Service {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -ne 'ergo-ollama') { throw 'Unexpected service name' }
    Update-FakeService
    if ($script:present) { [pscustomobject]@{ Status = $script:status } }
}

function Start-Service {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -ne 'ergo-ollama' -or -not $script:installed -or -not $script:present) { throw 'Start before successful installation' }
    $script:calls.Add('start-service')
    if ($Scenario -eq 'start-error') { throw 'Simulated start failure' }
    $script:started = $true
}

# Unexpected service-control paths fail closed, without reaching Windows cmdlets.
function Stop-Service { throw 'Direct Stop-Service is forbidden in this harness' }
function Restart-Service { throw 'Restart-Service is forbidden in this harness' }
function New-Service { throw 'New-Service is forbidden in this harness' }
function Remove-Service { throw 'Remove-Service is forbidden in this harness' }
function Set-Service { throw 'Set-Service is forbidden in this harness' }

function Get-Date { $script:clock }
function Start-Sleep {
    param([int]$Seconds, [int]$Milliseconds)
    $script:sleeps++
    if ($script:sleeps -gt 256) { throw 'Unbounded service wait' }
    $script:clock = $script:clock.AddSeconds($Seconds + $Milliseconds / 1000.0)
}
function Write-ColorOutput {
    param([string]$Message, [string]$Color)
    $script:messages.Add($Message)
}
function Get-ProjectWrappersDir { param([string]$ProjectRoot) Join-Path $ProjectRoot 'core\deployment\wrappers' }
function Get-ProjectLogsDir { param([string]$ProjectRoot) Join-Path $ProjectRoot 'logs' }
function Show-ServicesStatus { param([string]$ProjectRoot) $script:calls.Add('show-status') }
function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -ne 'ergoms') { throw 'Unexpected command discovery' }
    if ($Scenario -ne 'missing-cli') { [pscustomobject]@{ Source = $script:cli } }
}
function Install-NSSM {
    param([Parameter(Mandatory = $true)][string]$Root)
    if ($Root -ne $script:testRoot) { throw 'Incorrect NSSM project root' }
    $script:calls.Add('install-nssm|' + $Root)
    'Invoke-FakeNssm'
}
function Invoke-FakeNssm {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
    Update-FakeService
    $script:calls.Add(($CommandArgs -join '|'))
    $global:LASTEXITCODE = 0
    $action = $CommandArgs[0]
    if ($CommandArgs[1] -ne 'ergo-ollama') { throw 'Unexpected NSSM service name' }
    if ($Scenario -eq ($action + '-error') -or ($Scenario -eq 'config-error' -and $action -eq 'set')) {
        $global:LASTEXITCODE = 5
        'Simulated NSSM failure'
        return
    }
    switch ($action) {
        'get' {
            if ($CommandArgs[2] -ne 'AppDirectory') { throw 'Unexpected ownership query' }
            if ($Scenario -eq 'other-root') { Join-Path (Split-Path -Parent $Root) 'another-project\core' }
            else { Join-Path $Root 'core' }
        }
        'stop' {
            $script:status = 'StopPending'
            $script:stopAt = if ($Scenario -eq 'stop-timeout') { $null } else { $script:clock.AddSeconds(6) }
        }
        'remove' {
            if ($script:status -ne 'Stopped') { throw 'Removed a service before it stopped' }
            $script:removing = $true
            $script:removeAt = if ($Scenario -eq 'remove-timeout') { $null } else { $script:clock.AddSeconds(4) }
        }
        'install' {
            if ($script:present) { throw 'Installed before the previous service disappeared' }
            $script:present = $true
            $script:status = 'Stopped'
            $script:installed = $true
            $script:removing = $false
            $script:removeAt = $null
            $script:stopAt = $null
        }
        'set' { if (-not $script:installed) { throw 'Configured before successful installation' } }
        default { throw 'Unexpected NSSM action' }
    }
}

function Read-Functions {
    param([string]$Path, [string[]]$Names)
    $tokens = $null
    $errors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors.Message -join '; ') }
    $functions = $tree.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
    foreach ($definition in $functions) {
        if (-not $Names -or $definition.Name -in $Names) {
            . ([scriptblock]::Create($definition.Extent.Text))
            Set-Item -Path ('Function:script:' + $definition.Name) -Value (Get-Item -Path ('Function:' + $definition.Name)).ScriptBlock
        }
    }
    return ,$functions
}

$script:testRoot = $Root
$ProjectRoot = $Root
$platform = Join-Path $PSScriptRoot '..\..\..\core\deployment\windows\lib\services.ps1'
$null = Read-Functions -Path $platform -Names @('Wait-ServiceStopped')
$definitions = Read-Functions -Path $Installer
$last = ($definitions | Sort-Object { $_.Extent.EndOffset } | Select-Object -Last 1).Extent.EndOffset
$source = [System.IO.File]::ReadAllText($Installer)
$entry = $source.Substring($last)
$entryTokens = $null
$entryErrors = $null
$entryAst = [System.Management.Automation.Language.Parser]::ParseInput($entry, [ref]$entryTokens, [ref]$entryErrors)
$allowed = @('Write-ColorOutput', 'Install-OllamaService', 'Start-Service', 'Show-ServicesStatus')
foreach ($command in $entryAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    if ($command.GetCommandName() -notin $allowed) { throw 'Unexpected installer entry point command' }
}
$failure = ''
try { & ([scriptblock]::Create($entry)) }
catch { $failure = $_.Exception.Message }

$wrapper = Join-Path (Get-ProjectWrappersDir -ProjectRoot $Root) 'start_ollama.bat'
$text = ''
$bom = $false
if (Test-Path -LiteralPath $wrapper) {
    $bytes = [System.IO.File]::ReadAllBytes($wrapper)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191
}
[pscustomobject]@{
    Error = $failure; Root = $Root; Calls = @($script:calls); Messages = @($script:messages)
    Started = $script:started; Wrapper = $text; WrapperHasBom = $bom; Sleeps = $script:sleeps
} | ConvertTo-Json -Depth 5 -Compress

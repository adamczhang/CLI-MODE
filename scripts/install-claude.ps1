# Install or update CLI-MODE for Claude Code from this extracted archive (Windows PowerShell 5.1).
#
# The archive is a one-plugin Claude Code marketplace. A local marketplace
# loads its plugin in place, so it is copied to one fixed folder and updated
# there. This script never removes a marketplace (that would uninstall the
# plugin and delete its saved conversations); it reinstalls with --keep-data.
[CmdletBinding()]
param(
    [string]$Destination=(Join-Path $env:LOCALAPPDATA 'CLI-MODE\claude-marketplace')
)
$ErrorActionPreference='Stop'
$MinimumClaude=[version]'2.1.147'
$Plugin='cli-mode@cli-mode'

function Require($name,$hint){
    if(!(Get-Command $name -ErrorAction SilentlyContinue)){throw ($name+' was not found. '+$hint)}
}
# Named Invoke-*: a PowerShell function called Claude would shadow the claude program itself.
function Invoke-Claude(){
    & $script:ClaudeExe @args
    if($LASTEXITCODE -ne 0){throw ('claude '+($args -join ' ')+' failed with exit code '+$LASTEXITCODE+'.')}
}
function Invoke-ClaudeJson(){
    # PowerShell 5.1 parses piped lines one at a time and returns a JSON array as a single object.
    $parsed=(Invoke-Claude @args | Out-String | ConvertFrom-Json)
    return ,@($parsed)
}

$source=Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath=Join-Path $source '.claude-plugin\marketplace.json'
if(!(Test-Path $manifestPath) -or !(Test-Path (Join-Path $source 'plugins\cli-mode'))){
    throw 'Run this script from the extracted cli-mode-claude archive.'
}
$manifest=Get-Content $manifestPath -Raw | ConvertFrom-Json
$version=$manifest.plugins[0].version

Require 'claude' 'Install Claude Code first: https://code.claude.com/docs'
Require 'python' 'Install Python 3.10 or later and add it to PATH.'
Require 'node' 'Install Node.js 22.13 or later.'
$ClaudeExe=(Get-Command claude -CommandType Application | Select-Object -First 1).Source
$claudeVersion=[version](((Invoke-Claude --version) -split ' ')[0])
if($claudeVersion -lt $MinimumClaude){
    throw ('Claude Code '+$MinimumClaude+' or later is required; found '+$claudeVersion+'. Run: claude update')
}

# An existing cli-mode marketplace from anywhere else is left alone.
$target=[IO.Path]::GetFullPath($Destination)
if($target.TrimEnd('\') -eq [IO.Path]::GetFullPath($source).TrimEnd('\')){
    throw 'Choose a -Destination other than the extracted archive folder.'
}
$known=(Invoke-ClaudeJson plugin marketplace list --json) | Where-Object {$_.name -eq 'cli-mode'} | Select-Object -First 1
if($known -and (!$known.path -or [IO.Path]::GetFullPath($known.path) -ne $target)){
    $from=if($known.path){$known.path}else{$known.source}
    throw ('A Claude Code marketplace named cli-mode already comes from '+$from+'. Update that one with: '+
           'claude plugin marketplace update cli-mode   (removing it would also uninstall the plugin and its saved data).')
}

# Replace the folder's contents with this version.
if(Test-Path $target){
    $existing=Join-Path $target '.claude-plugin\marketplace.json'
    if(!(Test-Path $existing) -or (Get-Content $existing -Raw | ConvertFrom-Json).name -ne 'cli-mode'){
        throw ($target+' exists and is not a CLI-MODE marketplace folder; choose another -Destination.')
    }
    Get-ChildItem -LiteralPath $target -Force | Remove-Item -Recurse -Force
}else{
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}
Copy-Item -LiteralPath (Join-Path $source '.claude-plugin') -Destination $target -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $target 'plugins') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'plugins\cli-mode') -Destination (Join-Path $target 'plugins') -Recurse -Force

if($known){Invoke-Claude plugin marketplace update cli-mode}else{Invoke-Claude plugin marketplace add $target}
$installed=(Invoke-ClaudeJson plugin list --json) | Where-Object {$_.id -eq $Plugin} | Select-Object -First 1
if($installed){
    # `plugin update` skips a build that keeps the same version, leaving Claude Code's cached copy stale.
    # Reinstall instead; --keep-data preserves CLI-MODE's saved conversations and settings.
    Invoke-Claude plugin uninstall $Plugin --keep-data
}
Invoke-Claude plugin install $Plugin

# Claude Code names a plugin's commands by plugin (/cli-mode:cli): add bare /cli and /d as personal commands,
# never replacing a file of the user's own. The same code runs for /cli shortcuts after a GitHub install.
& python (Join-Path $target 'plugins\cli-mode\scripts\claude_shortcuts.py')
if($LASTEXITCODE -ne 0){throw 'Adding the /cli and /d shortcuts failed.'}

Write-Host ('CLI-MODE '+$version+' is installed for Claude Code from '+$target+'.')
Write-Host 'Start a new Claude Code session (or run /reload-plugins), accept the folder''s workspace trust prompt, then type /cli.'

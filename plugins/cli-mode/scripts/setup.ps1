# Dependency bootstrap for Windows PowerShell 5.1; Python is not required.
[CmdletBinding()]
param(
    [ValidateSet('Scan','Start','Run','Status','Cancel','Manual')][string]$Action='Scan',
    [ValidateSet('agy','claude','grok-build','cursor','copilot','codex')][string]$Backend='agy',
    [switch]$Approved,
    [string]$RunId,
    [string]$SetupRoot=(Join-Path $env:LOCALAPPDATA 'CLI-MODE\setup')
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
# The host that started setup (CLI_MODE_HOST), named in the messages it shows.
$HostName=if($env:CLI_MODE_HOST -eq 'claude-code'){'Claude Code'}else{'Codex'}
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$manual='After installing the missing components, run /cli again to resume setup.'
$links=[ordered]@{
    Python='https://www.python.org/downloads/windows/'
    PowerShell='https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows'
    Node='https://nodejs.org/en/download'
    ACPX='https://github.com/openclaw/acpx#installation'
    Antigravity='https://antigravity.google/docs/cli/install/'
    ACP='https://github.com/openclaw/acpx/blob/main/agents/Antigravity.md'
    ClaudeCode='https://docs.claude.com/en/docs/claude-code/setup'
    ClaudeACP='https://github.com/openclaw/acpx/blob/main/agents/Claude.md'
    GrokBuild='https://docs.x.ai/build/overview'
    GrokACP='https://github.com/openclaw/acpx/blob/main/agents/GrokBuild.md'
    Cursor='https://cursor.com/docs/cli/overview'
    CursorACP='https://github.com/openclaw/acpx/blob/main/agents/Cursor.md'
    Codex='https://learn.chatgpt.com/docs/codex/cli'
    Copilot='https://docs.github.com/copilot/how-tos/copilot-chat/use-copilot-chat-in-the-command-line'
    CopilotACP='https://github.com/openclaw/acpx/blob/main/agents/Copilot.md'
}
# CLI-MODE installs its own pinned ACPX with npm ci from the plugin's lockfile.
# acpx.py owned_root() computes the same default and override.
$AcpxVersion='0.18.0'
$AcpxRoot=if($env:CLI_MODE_ACPX_ROOT){$env:CLI_MODE_ACPX_ROOT}else{Join-Path $env:LOCALAPPDATA ('CLI-MODE\acpx\'+$AcpxVersion)}
$AcpxSpec=Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime\acpx'
function Refresh-Paths {
    $env:PATH=(@((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313'),(Join-Path $env:ProgramFiles 'nodejs'),[Environment]::GetEnvironmentVariable('PATH','User'),[Environment]::GetEnvironmentVariable('PATH','Machine'),$env:PATH,(Join-Path $env:LOCALAPPDATA 'agy\bin'),(Join-Path $env:APPDATA 'npm'),(Join-Path $env:USERPROFILE '.grok\bin'),(Join-Path $env:LOCALAPPDATA 'cursor-agent')) | Where-Object {$_}) -join ';'
}
function Probe([string]$name,[string]$binary) {
    $command=Get-Command $binary -ErrorAction SilentlyContinue | Select-Object -First 1
    $ok=$false
    if($command -and $command.Source -notmatch 'WindowsApps\\python'){try{
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=$command.Source;$info.Arguments='--version'
        if($command.Source.EndsWith('.cmd')){
            $info.FileName=$env:ComSpec
            $info.Arguments='/d /s /c ""'+$command.Source+'" --version"'
        }
        $info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
        $probe=New-Object Diagnostics.Process;$probe.StartInfo=$info
        [void]$probe.Start()
        $stdout=$probe.StandardOutput.ReadToEndAsync();$stderr=$probe.StandardError.ReadToEndAsync()
        if(!$probe.WaitForExit(15000)){$probe.Kill();throw 'Version probe timed out.'}
        $output=$stdout.Result.Trim();$ok=$probe.ExitCode -eq 0
        if($name -eq 'Node.js'){$ok=$ok -and ([version]($output.TrimStart('v')) -ge [version]'22.13.0')}
        if($name -eq 'ACPX'){$ok=$ok -and ($output.TrimStart('v') -eq '0.18.0')}
        if($name -eq 'Python'){$ok=$ok -and ([version]($output -replace '^Python ','') -ge [version]'3.10')}
    }catch{$ok=$false}finally{if($probe){$probe.Dispose()}}}
    [pscustomobject]@{name=$name;installed=$ok}
}
function Probe-Acpx {
    # Prefer CLI-MODE's own copy; a global acpx@0.18.0 remains a supported fallback.
    $cli=Join-Path $AcpxRoot 'node_modules\acpx\dist\cli.js'
    $node=Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1
    if($node -and (Test-Path -LiteralPath $cli)){try{
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=$node.Source;$info.Arguments='"'+$cli+'" --version'
        $info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
        $probe=New-Object Diagnostics.Process;$probe.StartInfo=$info
        [void]$probe.Start()
        $stdout=$probe.StandardOutput.ReadToEndAsync();$stderr=$probe.StandardError.ReadToEndAsync()
        if(!$probe.WaitForExit(15000)){$probe.Kill();throw 'Version probe timed out.'}
        if($probe.ExitCode -eq 0 -and $stdout.Result.Trim().TrimStart('v') -eq $AcpxVersion){
            return [pscustomobject]@{name='ACPX';installed=$true;source='owned'}
        }
    }catch{}finally{if($probe){$probe.Dispose()}}}
    $global=Probe 'ACPX' 'acpx.cmd'
    [pscustomobject]@{name='ACPX';installed=$global.installed;source=$(if($global.installed){'global'}else{$null})}
}
function Install-Acpx {
    if(!(Test-Path -LiteralPath (Join-Path $AcpxSpec 'package-lock.json'))){throw 'The pinned ACPX lockfile is missing from this plugin copy.'}
    New-Item -ItemType Directory -Path $AcpxRoot -Force | Out-Null
    foreach($name in @('package.json','package-lock.json')){Copy-Item -LiteralPath (Join-Path $AcpxSpec $name) -Destination $AcpxRoot -Force}
    Push-Location -LiteralPath $AcpxRoot
    try{& npm.cmd ci --omit=dev --no-audit --no-fund;$code=$LASTEXITCODE}finally{Pop-Location}
    if($code -ne 0){throw 'ACPX install failed.'}
    if((Probe-Acpx).source -ne 'owned'){throw 'ACPX was installed but did not report version '+$AcpxVersion+'.'}
}
function ProbeAny([string]$name,[string[]]$binaries) {
    foreach($binary in $binaries){$result=Probe $name $binary;if($result.installed){return $result}}
    [pscustomobject]@{name=$name;installed=$false}
}
function Claude-SignedIn {
    # Structured, read-only and zero-turn: no model request and no quota spend.
    $command=Get-Command 'claude.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1
    if(!$command){$command=Get-Command 'claude.exe' -ErrorAction SilentlyContinue | Select-Object -First 1}
    if(!$command){return $false}
    try{
        $raw=& $env:ComSpec /d /s /c ('"'+$command.Source+'" auth status --json')
        if($LASTEXITCODE -ne 0){return $false}
        $status=($raw -join "`n") | ConvertFrom-Json
        return [bool]$status.loggedIn
    }catch{return $false}
}
function Grok-SignedIn {
    # Read-only: lists the account's models and exits without a model request.
    # It contacts grok.com, so one failed call is retried before reporting sign-out.
    $command=Get-Command 'grok.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if(!$command){$command=Get-Command 'grok.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1}
    if(!$command){return $false}
    foreach($attempt in 1,2){
        try{
            $raw=& $env:ComSpec /d /s /c ('"'+$command.Source+'" models')
            if($LASTEXITCODE -eq 0 -and (($raw -join "`n") -match 'logged in')){return $true}
        }catch{}
        if($attempt -eq 1){Start-Sleep -Milliseconds 800}
    }
    return $false
}
function Cursor-SignedIn {
    # Read-only auth check: no model request and no quota spend.
    $command=Get-Command 'cursor-agent.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1
    if(!$command){$command=Get-Command 'cursor-agent.exe' -ErrorAction SilentlyContinue | Select-Object -First 1}
    if(!$command){return $false}
    try{
        $raw=& $env:ComSpec /d /s /c ('"'+$command.Source+'" status')
        if($LASTEXITCODE -ne 0){return $false}
        return (($raw -join "`n") -match 'Logged in')
    }catch{return $false}
}
function Copilot-SignedIn {
    # Read-only auth check: no model request and no credit spend.
    $command=Get-Command 'copilot.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1
    if(!$command){$command=Get-Command 'copilot.exe' -ErrorAction SilentlyContinue | Select-Object -First 1}
    if(!$command){return $false}
    try{
        # No status subcommand exists; a successful version call plus a stored
        # token directory is the strongest read-only signal available.
        $raw=& $env:ComSpec /d /s /c ('"'+$command.Source+'" --version')
        if($LASTEXITCODE -ne 0){return $false}
        return (Test-Path (Join-Path $env:USERPROFILE '.copilot'))
    }catch{return $false}
}
function Codex-SignedIn {
    $command=Get-Command 'codex.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1
    if(!$command){$command=Get-Command 'codex.exe' -ErrorAction SilentlyContinue | Select-Object -First 1}
    if(!$command){return $false}
    # Codex prints successful login status to stderr. Windows PowerShell 5.1
    # turns redirected stderr into an error record; use the exit code instead.
    $previousPreference=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    try { & $command.Source login status 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
    finally { $ErrorActionPreference=$previousPreference }
}
function Scan {
    Refresh-Paths
    $checks=@(Probe 'Python' 'python';Probe 'Node.js' 'node';Probe 'npm' 'npm.cmd';Probe 'npx' 'npx.cmd';Probe-Acpx)
    $configPath=Join-Path $env:USERPROFILE '.acpx\config.json'
    $profileName=switch($Backend){'agy'{'antigravity'}'claude'{'claude'}'cursor'{'cursor'}'copilot'{'copilot'}'codex'{'codex'}default{'grok-build'}}
    $custom=$false;$managed=$false
    if(Test-Path $configPath){
        $config=Get-Content $configPath -Raw | ConvertFrom-Json
        if($config.agents -and $config.agents.$profileName){
            $custom=$true
            $managed=$config.agents.$profileName.argv -contains (Join-Path $SetupRoot 'runtime\acp-login.py')
        }
    }
    $customLauncher=$custom
    if($Backend -eq 'agy'){
        $checks += Probe 'Antigravity CLI' 'agy'
        $runtime=Join-Path $SetupRoot 'runtime'
        $checks += [pscustomobject]@{name='Antigravity ACP';installed=($custom -and (!$managed -or ((Test-Path (Join-Path $runtime 'agy_acp_server.exe')) -and (Test-Path (Join-Path $runtime 'localharness_external.exe')))))}
        $customLauncher=($custom -and !$managed)
    }elseif($Backend -eq 'claude'){
        # ACPX manages the Claude ACP adapter package itself, so there is no
        # separate runtime download. Sign-in is the remaining prerequisite.
        $checks += ProbeAny 'Claude Code CLI' @('claude.cmd','claude.exe')
        $checks += [pscustomobject]@{name='Claude Code sign-in';installed=(Claude-SignedIn)}
    }elseif($Backend -eq 'grok-build'){
        # ACPX launches the installed grok CLI through its own ACP stdio
        # entrypoint, so there is no separate adapter package or runtime.
        $checks += ProbeAny 'Grok Build CLI' @('grok.exe','grok.cmd')
        $checks += [pscustomobject]@{name='Grok Build sign-in';installed=(Grok-SignedIn)}
    }elseif($Backend -eq 'cursor'){
        # The installed Cursor CLI speaks ACP itself: no adapter package and no
        # separate runtime download.
        $checks += ProbeAny 'Cursor CLI' @('cursor-agent.cmd','cursor-agent.exe')
        $checks += [pscustomobject]@{name='Cursor sign-in';installed=(Cursor-SignedIn)}
    }elseif($Backend -eq 'codex'){
        $checks += ProbeAny 'Codex CLI' @('codex.cmd','codex.exe')
        $checks += [pscustomobject]@{name='Codex sign-in';installed=(Codex-SignedIn)}
    }else{
        # The installed Copilot CLI speaks ACP itself, but only on a release
        # that supports --acp --stdio.
        $checks += ProbeAny 'GitHub Copilot CLI' @('copilot.cmd','copilot.exe')
        $checks += [pscustomobject]@{name='Copilot sign-in';installed=(Copilot-SignedIn)}
    }
    @{checks=$checks;confirmed=(@($checks | Where-Object {!$_.installed}).Count -eq 0);customACP=$customLauncher;backend=$Backend;authentication='Verified during approved sign-in and activation';manual=$manual;links=$links}
}
function Save-Status($value) {
    $temporary=$statusPath+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    $value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}
function Canceled {Test-Path -LiteralPath ($statusPath+'.cancel')}
function Step([string]$name,[scriptblock]$work) {
    if(Canceled){throw 'Setup canceled.'}
    $script:state.component=$name;Save-Status $state
    Write-Host "`n$name" -ForegroundColor Green
    & $work
    if(Canceled){throw 'Setup canceled.'}
    Refresh-Paths
}
function Winget([string]$id) {
    if(!(Get-Command winget.exe -ErrorAction SilentlyContinue)){throw 'Windows App Installer (winget) is missing. Use the manual installation links.'}
    & winget.exe install --id $id --exact --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity
    if($LASTEXITCODE -ne 0){throw "Installation failed: $id (exit $LASTEXITCODE)."}
}
function Install-ACP {
    $runtime=Join-Path $SetupRoot 'runtime'
    $agent=Invoke-RestMethod 'https://raw.githubusercontent.com/agentclientprotocol/registry/main/antigravity-acp/agent.json'
    $arch=if($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64'){'windows-aarch64'}else{'windows-x86_64'}
    $url=[uri]$agent.distribution.binary.$arch.archive
    if($url.Scheme -ne 'https' -or $url.Host -ne 'dl.google.com' -or !$url.AbsolutePath.StartsWith('/agy-extensions/releases/')){throw 'Unexpected ACP distribution source.'}
    $staging=Join-Path $SetupRoot ('download-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $zip=Join-Path $staging 'runtime.zip'
    Invoke-WebRequest $url.AbsoluteUri -OutFile $zip -UseBasicParsing
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive=[IO.Compression.ZipFile]::OpenRead($zip)
    try{foreach($entry in $archive.Entries){
        $target=[IO.Path]::GetFullPath((Join-Path $staging $entry.FullName))
        if(!$target.StartsWith($staging+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe archive path.'}
    }}finally{$archive.Dispose()}
    Expand-Archive -LiteralPath $zip -DestinationPath $staging
    $server=@(Get-ChildItem -LiteralPath $staging -Filter agy_acp_server.exe -Recurse)
    if($server.Count -ne 1){throw 'ACP archive executable is missing or ambiguous.'}
    if(!(Test-Path (Join-Path $server[0].DirectoryName 'localharness_external.exe'))){throw 'Matching ACP helper is missing.'}
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    Copy-Item -Path (Join-Path $server[0].DirectoryName '*') -Destination $runtime -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'acp-login.py') -Destination $runtime -Force
    $configPath=Join-Path $env:USERPROFILE '.acpx\config.json'
    $config=if(Test-Path $configPath){Get-Content $configPath -Raw | ConvertFrom-Json}else{[pscustomobject]@{}}
    if(!$config.PSObject.Properties['agents']){$config | Add-Member agents ([pscustomobject]@{})}
    if($config.agents.PSObject.Properties['antigravity']){
        if($config.agents.antigravity.argv -notcontains (Join-Path $runtime 'acp-login.py')){throw 'Custom ACP launcher preserved. Configure it separately.'}
        $config.agents.PSObject.Properties.Remove('antigravity')
    }
    $config.agents | Add-Member antigravity ([pscustomobject]@{argv=@((Get-Command python).Source,(Join-Path $runtime 'acp-login.py'))})
    New-Item -ItemType Directory -Path (Split-Path $configPath) -Force | Out-Null
    if(Test-Path $configPath){Copy-Item $configPath ($configPath+'.backup-'+$RunId)}
    [IO.File]::WriteAllText(($configPath+'.tmp'),($config | ConvertTo-Json -Depth 100),(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath ($configPath+'.tmp') -Destination $configPath -Force
}
if($Action -eq 'Manual'){@{message=$manual;links=$links;acpxCommand=('npm install --prefix "'+$AcpxRoot+'" acpx@'+$AcpxVersion)} | ConvertTo-Json -Depth 8;exit}
if($Action -eq 'Scan'){Scan | ConvertTo-Json -Depth 8;exit}
if($RunId -and $RunId -notmatch '^[a-f0-9]{32}$'){throw 'Invalid setup run ID.'}
New-Item -ItemType Directory -Path $SetupRoot -Force | Out-Null
$statusPath=Join-Path $SetupRoot 'status.json'
if($Action -eq 'Status' -or $Action -eq 'Cancel'){
    if(!(Test-Path $statusPath)){throw 'No setup run exists.'}
    $state=Get-Content $statusPath -Raw | ConvertFrom-Json
    if($RunId -and $state.runId -ne $RunId){throw 'Different setup run; no changes made.'}
    if($Action -eq 'Cancel'){
        New-Item -ItemType File -Path ($statusPath+'.cancel') -Force | Out-Null
        $state | Add-Member -Force cancelRequested $true
        $state.message='Cancellation requested; the current package operation may finish before stopping. Close the window to interrupt immediately.'
    }
    if($state.status -in @('running','starting') -and !(Get-Process -Id $state.pid -ErrorAction SilentlyContinue)){$state.status='interrupted';$state.message=$manual}
    $state | ConvertTo-Json -Depth 10;exit
}
if(!$Approved){throw 'User approval is required before installation or sign-in.'}
$lockPath=Join-Path $SetupRoot 'installer.lock'
if($Action -eq 'Start'){
    $guard=[IO.File]::Open($lockPath,'OpenOrCreate','ReadWrite','None')
    try{
        if(Test-Path $statusPath){
            $old=Get-Content $statusPath -Raw | ConvertFrom-Json
            if($old.status -in @('running','starting') -and (Get-Process -Id $old.pid -ErrorAction SilentlyContinue)){throw 'An installer is already running. Monitor it instead.'}
        }
        $RunId=[guid]::NewGuid().ToString('N')
        if(Test-Path ($statusPath+'.cancel')){Remove-Item -LiteralPath ($statusPath+'.cancel')}
        $quote={param($s) "'"+$s.Replace("'","''")+"'"}
        $command='& '+(& $quote $PSCommandPath)+' -Action Run -Approved -RunId '+$RunId+' -SetupRoot '+(& $quote $SetupRoot)
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $shell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $process=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-EncodedCommand',$encoded) -WindowStyle Normal -PassThru
        Save-Status @{runId=$RunId;pid=$process.Id;status='starting';component='Opening setup';message='Waiting for PowerShell';links=$links}
    }finally{$guard.Dispose()}
    @{runId=$RunId;pid=$process.Id;status='starting';statusPath=$statusPath} | ConvertTo-Json;exit
}
$guard=$null
for($attempt=0;$attempt -lt 30 -and !$guard;$attempt++){try{$guard=[IO.File]::Open($lockPath,'OpenOrCreate','ReadWrite','None')}catch{Start-Sleep -Milliseconds 100}}
if(!$guard){throw 'An installer is already running.'}
$state=@{runId=$RunId;pid=$PID;status='running';component='Scan';message='Installing approved missing components';links=$links}
try{
    Save-Status $state
    $scan=Scan
    $missing=@($scan.checks | Where-Object {!$_.installed} | ForEach-Object {$_.name})
    foreach($entry in @(@('Python','Python.Python.3.13'),@('Node.js','OpenJS.NodeJS.LTS'))){
        if($missing -contains $entry[0]){$id=$entry[1];Step $entry[0] {Winget $id}}
    }
    if($missing -contains 'npm' -and $missing -notcontains 'Node.js'){Step 'Repair Node.js/npm' {Winget 'OpenJS.NodeJS.LTS'}}
    if($missing -contains 'npx' -and $missing -notcontains 'Node.js' -and $missing -notcontains 'npm'){Step 'Repair npx' {Winget 'OpenJS.NodeJS.LTS'}}
    Refresh-Paths
    if($missing -contains 'ACPX'){Step 'ACPX' {Install-Acpx}}
    if($Backend -eq 'agy'){
        if($missing -contains 'Antigravity CLI'){Step 'Antigravity CLI' {
            $installer=Join-Path $SetupRoot ('agy-install-'+$RunId+'.ps1')
            Invoke-WebRequest 'https://antigravity.google/cli/install.ps1' -OutFile $installer -UseBasicParsing
            & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -File $installer
            if($LASTEXITCODE -ne 0){throw 'Antigravity install failed.'}
        }}
        if($missing -contains 'Antigravity ACP'){Step 'Antigravity ACP' {Install-ACP}}
    }elseif($Backend -eq 'claude'){
        if($missing -contains 'Claude Code CLI'){Step 'Claude Code CLI' {
            & npm.cmd install -g @anthropic-ai/claude-code
            if($LASTEXITCODE -ne 0){throw 'Claude Code install failed.'}
        }}
    }elseif($Backend -eq 'grok-build'){
        # No verified unattended installer is published for Grok Build, so it is
        # never downloaded from a guessed source. Sign-in below is interactive.
        if($missing -contains 'Grok Build CLI'){throw 'Install Grok Build separately from '+$links.GrokBuild+', then rerun /cli.'}
    }elseif($Backend -eq 'cursor'){
        if($missing -contains 'Cursor CLI'){throw 'Install the Cursor CLI separately from '+$links.Cursor+', then rerun /cli.'}
    }elseif($Backend -eq 'codex'){
        if($missing -contains 'Codex CLI'){Step 'Codex CLI' {
            & npm.cmd install -g @openai/codex
            if($LASTEXITCODE -ne 0){throw 'Codex CLI install failed.'}
        }}
    }else{
        if($missing -contains 'GitHub Copilot CLI'){Step 'GitHub Copilot CLI' {
            & npm.cmd install -g @github/copilot
            if($LASTEXITCODE -ne 0){throw 'Copilot CLI install failed. Install it from '+$links.Copilot+', then rerun /cli.'}
        }}
    }
    $scan=Scan
    $missing=@($scan.checks | Where-Object {!$_.installed} | ForEach-Object {$_.name})
    if($Backend -eq 'agy'){
        if(@($missing | Where-Object {$_ -ne 'Antigravity ACP'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        Step 'Antigravity sign-in' {
            Write-Host 'Complete Google sign-in in your browser if it opens. No agent task will run.'
            & python (Join-Path $PSScriptRoot 'setup_auth.py') --native
            if($LASTEXITCODE -ne 0){throw 'Native sign-in was not verified. Run agy separately to sign in, then recheck.'}
        }
        if($scan.customACP){throw 'Custom ACP launcher preserved. Complete its sign-in separately, then run /cli again.'}
        Step 'ACP sign-in' {
            & python (Join-Path $SetupRoot 'runtime\acp-login.py') --login
            if($LASTEXITCODE -ne 0){throw 'ACP sign-in was not verified. Follow the ACP manual setup link.'}
        }
    }elseif($Backend -eq 'claude'){
        if(@($missing | Where-Object {$_ -ne 'Claude Code sign-in'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        if($missing -contains 'Claude Code sign-in'){
            Step 'Claude Code sign-in' {
                Write-Host 'Complete Claude sign-in in your browser if it opens. No agent task will run.'
                Write-Host 'A Claude subscription signs in here; an API key is billed separately.'
                & $env:ComSpec /d /s /c 'claude.cmd setup-token'
                if($LASTEXITCODE -ne 0){throw 'Claude sign-in was not verified. Run claude setup-token separately, then recheck.'}
            }
        }
        # ACPX manages the Claude ACP adapter package; a configured custom
        # launcher is preserved and signs in on its own terms.
        if($scan.customACP){Write-Host 'Custom ACP launcher for the claude profile preserved; CLI-MODE did not change it.'}
        $scan=Scan
        if(!$scan.confirmed){throw 'Claude Code sign-in is still unverified. Sign in separately, then recheck.'}
    }elseif($Backend -eq 'grok-build'){
        if(@($missing | Where-Object {$_ -ne 'Grok Build sign-in'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        if($missing -contains 'Grok Build sign-in'){
            Step 'Grok Build sign-in' {
                Write-Host 'Complete Grok sign-in in your browser if it opens. No agent task will run.'
                & $env:ComSpec /d /s /c 'grok.exe login'
                if($LASTEXITCODE -ne 0){throw 'Grok sign-in was not verified. Run grok login separately, then recheck.'}
            }
        }
        if($scan.customACP){Write-Host 'Custom ACP launcher for the grok-build profile preserved; CLI-MODE did not change it.'}
        $scan=Scan
        if(!$scan.confirmed){throw 'Grok Build sign-in is still unverified. Sign in separately, then recheck.'}
    }elseif($Backend -eq 'cursor'){
        if(@($missing | Where-Object {$_ -ne 'Cursor sign-in'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        if($missing -contains 'Cursor sign-in'){
            Step 'Cursor sign-in' {
                Write-Host 'Complete Cursor sign-in in your browser if it opens. No agent task will run.'
                & $env:ComSpec /d /s /c 'cursor-agent.cmd login'
                if($LASTEXITCODE -ne 0){throw 'Cursor sign-in was not verified. Run cursor-agent login separately, then recheck.'}
            }
        }
        if($scan.customACP){Write-Host 'Custom ACP launcher for the cursor profile preserved; CLI-MODE did not change it.'}
        $scan=Scan
        if(!$scan.confirmed){throw 'Cursor sign-in is still unverified. Sign in separately, then recheck.'}
    }elseif($Backend -eq 'codex'){
        if(@($missing | Where-Object {$_ -ne 'Codex sign-in'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        if($missing -contains 'Codex sign-in'){
            Step 'Codex sign-in' {
                & codex login
                if($LASTEXITCODE -ne 0){throw 'Codex sign-in was not verified. Run codex login separately, then recheck.'}
            }
        }
        if($scan.customACP){Write-Host 'Custom ACP launcher for the codex profile preserved; CLI-MODE did not change it.'}
        $scan=Scan
        if(!$scan.confirmed){throw 'Codex sign-in is still unverified. Sign in separately, then recheck.'}
    }else{
        if(@($missing | Where-Object {$_ -ne 'Copilot sign-in'}).Count){throw 'Some tools are still missing or cannot run. Install them manually and recheck.'}
        if($missing -contains 'Copilot sign-in'){
            Step 'Copilot sign-in' {
                Write-Host 'Complete GitHub sign-in in your browser if it opens. No agent task will run.'
                & $env:ComSpec /d /s /c 'copilot.cmd login'
                if($LASTEXITCODE -ne 0){throw 'Copilot sign-in was not verified. Run copilot login separately, then recheck.'}
            }
        }
        if($scan.customACP){Write-Host 'Custom ACP launcher for the copilot profile preserved; CLI-MODE did not change it.'}
        $scan=Scan
        if(!$scan.confirmed){throw 'Copilot sign-in is still unverified. Sign in separately, then recheck.'}
    }
    $state.status='complete';$state.component='Verified';$state.message='Setup complete. Return to '+$HostName+' for Agent Settings.'
}catch{
    $state.status=if(Canceled){'canceled'}else{'failed'}
    $state.message=$_.Exception.Message+' '+$manual
    Write-Host $state.message -ForegroundColor Yellow
    Write-Host ('Options: Retry in '+$HostName+', install manually/separately, or exit setup.')
    $links.GetEnumerator() | ForEach-Object {Write-Host ($_.Key+': '+$_.Value)}
}finally{Save-Status $state;$guard.Dispose()}
if($state.status -ne 'complete'){Read-Host 'Press Enter to close this window' | Out-Null}

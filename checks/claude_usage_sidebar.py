"""The `sidebar` task set for checks/claude_usage_live.py: three realistic changes to Sidebar Diagnostics, an open-source
WPF (.NET Framework 4.7.2) hardware sidebar of about 13k lines, instead of synthetic prompts. Z5, Z10 and Z20 ask for
about 5k, 10k and 20k tokens of work. Each run builds with the Visual Studio Build Tools MSBuild, runs the project's own
PowerShell test, and runs hidden PowerShell tests (never shown to the agents) that load the built app.

The seed is copied from a local checkout (SIDEBAR_UPSTREAM, default below): its source into a fresh git repository, and
its NuGet packages and LibreHardwareMonitor submodule beside it, git-ignored so change receipts stay small."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NAMES = ('Z5', 'Z10', 'Z20')
UPSTREAM = Path(os.environ.get('SIDEBAR_UPSTREAM') or r'C:\Users\adamc\Desktop\ChatGPT\Sidebar\SidebarDiagnostics-upstream')
MSBUILD = Path(os.environ.get('SIDEBAR_MSBUILD') or
               r'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe')
NO_QUESTIONS = ' Work without asking me questions: make reasonable assumptions and say what they were. Do not commit.'
BUILD = '"' + str(MSBUILD) + '" SidebarDiagnostics\\SidebarDiagnostics.csproj -p:Configuration=Release -nologo -v:q -m'
BUILDING = ('# Building\n\nBuild the Release configuration with the Visual Studio Build Tools MSBuild (`dotnet build` cannot '
            'compile this WPF project):\n\n    ' + BUILD + '\n\nThen run the tests, which load the built app:\n\n'
            '    powershell.exe -NoProfile -STA -File tests\\BackgroundLayout.ps1\n')
IGNORED = ('packages/', 'LibreHardwareMonitor/', 'bin/', 'obj/', 'Agent_Working_Folder/', '.vs/', '*.user')


def seed(workspace):
    """A fresh repository of the upstream source, with its build dependencies beside it (git-ignored)."""
    workspace = Path(workspace)
    skip = shutil.ignore_patterns('.git', 'bin', 'obj', '.vs')
    for entry in UPSTREAM.iterdir():
        if entry.name in ('.git', '.gitmodules', 'packages', 'LibreHardwareMonitor'):
            continue
        target = workspace / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, ignore=skip)
        else:
            shutil.copy2(entry, target)
    shutil.copytree(UPSTREAM / 'packages', workspace / 'packages')
    shutil.copytree(UPSTREAM / 'LibreHardwareMonitor', workspace / 'LibreHardwareMonitor',
                    ignore=shutil.ignore_patterns('.git'))  # Its own build output stays: the app's build reuses it.
    (workspace / 'BUILDING.md').write_text(BUILDING, encoding='utf-8')
    ignore = workspace / '.gitignore'
    old = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    ignore.write_text(old.rstrip('\n') + '\n' + '\n'.join(IGNORED) + '\n', encoding='utf-8')
    for command in (['init', '-q'], ['add', '-A'],
                    ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Seed']):
        subprocess.run(['git', *command], cwd=workspace, check=True, capture_output=True)


def tasks():
    return {
        'Z5': ('Users outside the US asked to see temperatures in Kelvin. Add a Kelvin option for the CPU and GPU '
               'temperatures: DataType.Kelvin (shown with a "K" suffix), a CelciusToKelvin converter in '
               'SidebarDiagnostics.Monitoring that follows CelciusToFahrenheit (a singleton Instance), and a UseKelvin '
               'parameter (ParamKey.UseKelvin, off by default) on the CPU and GPU monitors, with a label and tooltip '
               'like UseFahrenheit\'s. When both UseFahrenheit and UseKelvin are on, Kelvin wins. Existing settings '
               'files must keep loading. Build it and keep tests\\BackgroundLayout.ps1 passing (see BUILDING.md).' +
               NO_QUESTIONS, {}),
        'Z10': ('People move between PCs and want to take their sidebar setup with them. Add settings export and '
                'import: a static class SettingsTransfer in SidebarDiagnostics.Framework with Export(Settings settings, '
                'string path) and Import(string path, Settings target). The file is indented JSON with a '
                '"FormatVersion": 1 field beside the settings. Machine-specific values stay on each machine: '
                'ScreenIndex, XOffset, YOffset and InitialSetup are not exported, and Import leaves them as they are. '
                'Import refuses a file that is not JSON, has no FormatVersion, or has a newer one, with an '
                'InvalidDataException whose message says what is wrong; unknown fields are ignored. Add Export... and '
                'Import... buttons to the Settings window, with save and open file dialogs; after an import the sidebar '
                'applies the new settings as it does after Save. Build it and keep tests\\BackgroundLayout.ps1 passing '
                '(see BUILDING.md).' + NO_QUESTIONS, {}),
        'Z20': ('Add optional metric logging, so a user can look back at what the sidebar showed.\n'
                '1. A class MetricLogger in SidebarDiagnostics.Framework: MetricLogger(string folder, long maxBytes, '
                'int keepFiles) and Log(DateTime time, IList<KeyValuePair<string, string>> values). It appends to '
                'metrics.csv in the folder (created if missing). The first line is a header: time, then each value\'s '
                'name. Each row is the time as yyyy-MM-dd HH:mm:ss, then the values, as CSV: a field with a comma, quote '
                'or line break is quoted, with quotes doubled. When the names differ from the file\'s header, or a row '
                'would take the file past maxBytes, the file rolls over: metrics.csv becomes metrics.1.csv, metrics.1.csv '
                'becomes metrics.2.csv and so on, keeping at most keepFiles old files (the oldest is deleted), and a new '
                'metrics.csv starts with its header.\n'
                '2. Settings: LogMetrics (default false), LogFolder (default %LOCALAPPDATA%\\SidebarDiagnostics\\logs), '
                'LogMaxKB (default 1024) and LogKeepFiles (default 5), in a Logging section of the Settings window with a '
                'folder picker.\n'
                '3. While LogMetrics is on, each monitoring update logs every visible metric (the monitor\'s name and '
                'the metric\'s label as the name, the displayed text as the value). A failure to write never stops the '
                'sidebar.\n'
                '4. A README section, and a PowerShell test tests\\MetricLogger.ps1 in the style of the existing one.\n'
                'Build it and keep tests\\BackgroundLayout.ps1 passing (see BUILDING.md).' + NO_QUESTIONS, {}),
    }


LOAD = ("$ErrorActionPreference = 'Stop'\nAdd-Type -AssemblyName PresentationFramework\n"
        "$output = Join-Path (Get-Location) 'SidebarDiagnostics/bin/Release'\n"
        "[void][Reflection.Assembly]::LoadFrom((Join-Path $output 'Newtonsoft.Json.dll'))\n"
        "[void][Reflection.Assembly]::LoadFrom((Join-Path $output 'SidebarDiagnostics.exe'))\n"
        "$script:passed = 0; $script:failed = 0\n"
        "function Check($name, $block) { try { if (& $block) { $script:passed++ } else { $script:failed++; "
        "Write-Output ('FAIL ' + $name) } } catch { $script:failed++; Write-Output ('FAIL ' + $name + ': ' + "
        "$_.Exception.Message) } }\n"
        "function Refused($e) { while ($e) { if ($e -is [IO.InvalidDataException]) { return $true }; "
        "$e = $e.InnerException }; $false }\n"
        "$settingsType = [SidebarDiagnostics.Framework.Settings]\n"
        "function NewSettings { [Newtonsoft.Json.JsonConvert]::DeserializeObject('{}', $settingsType) }\n")
END = "Write-Output ('HIDDEN ' + $script:passed + '/' + ($script:passed + $script:failed))\n"

HIDDEN = {
    'Z5': LOAD + (
        "Check 'kelvin converter' { $c = [SidebarDiagnostics.Monitoring.CelciusToKelvin]::Instance; [double]$v = 100; "
        "$c.Convert([ref]$v); [math]::Abs($v - 373.15) -lt 1e-9 }\n"
        "Check 'kelvin target type' { [SidebarDiagnostics.Monitoring.CelciusToKelvin]::Instance.TargetType -eq "
        "[SidebarDiagnostics.Monitoring.DataType]::Kelvin }\n"
        "Check 'UseKelvin parameter' { [enum]::IsDefined([SidebarDiagnostics.Monitoring.ParamKey], 'UseKelvin') }\n"
        "Check 'fahrenheit unchanged' { $c = [SidebarDiagnostics.Monitoring.CelciusToFahrenheit]::Instance; "
        "[double]$v = 100; $c.Convert([ref]$v); [math]::Abs($v - 212) -lt 1e-9 }\n") + END,
    'Z10': LOAD + (
        "$a = NewSettings; $a.BGColor = '#112233'; $a.UIScale = 1.25; $a.ScreenIndex = 2; $a.XOffset = 40\n"
        "$path = [IO.Path]::GetTempFileName()\n"
        "Check 'export' { [SidebarDiagnostics.Framework.SettingsTransfer]::Export($a, $path); Test-Path $path }\n"
        "$text = Get-Content $path -Raw\n"
        "Check 'format version' { $text -match '\"FormatVersion\"\\s*:\\s*1' }\n"
        "Check 'no machine values' { -not ($text -match '\"(ScreenIndex|XOffset|YOffset|InitialSetup)\"') }\n"
        "$b = NewSettings; $b.ScreenIndex = 5; $b.XOffset = 7\n"
        "Check 'import applies' { [SidebarDiagnostics.Framework.SettingsTransfer]::Import($path, $b); "
        "$b.BGColor -eq '#112233' -and $b.UIScale -eq 1.25 }\n"
        "Check 'import keeps machine values' { $b.ScreenIndex -eq 5 -and $b.XOffset -eq 7 }\n"
        "$bad = [IO.Path]::GetTempFileName(); Set-Content $bad 'not json'\n"
        "Check 'bad json refused' { try { [SidebarDiagnostics.Framework.SettingsTransfer]::Import($bad, (NewSettings)); "
        "$false } catch { Refused $_.Exception } }\n"
        "Set-Content $bad '{\"FormatVersion\": 99, \"BGColor\": \"#000000\"}'\n"
        "Check 'newer version refused' { try { [SidebarDiagnostics.Framework.SettingsTransfer]::Import($bad, "
        "(NewSettings)); $false } catch { Refused $_.Exception } }\n") + END,
    'Z20': LOAD + (
        "$dir = Join-Path ([IO.Path]::GetTempPath()) ('mlog-' + [guid]::NewGuid())\n"
        "function Pairs($names, $values) { $list = New-Object 'System.Collections.Generic.List[System.Collections.Generic."
        "KeyValuePair[string,string]]'; for ($i = 0; $i -lt $names.Count; $i++) { $list.Add((New-Object "
        "'System.Collections.Generic.KeyValuePair[string,string]' $names[$i], $values[$i])) }; ,$list }\n"
        "$logger = [SidebarDiagnostics.Framework.MetricLogger]::new($dir, 400, 2)\n"
        "$logger.Log([datetime]'2026-09-28 10:00:00', (Pairs @('CPU Load', 'Note') @('12%', 'a,b \"c\"')))\n"
        "$lines = Get-Content (Join-Path $dir 'metrics.csv')\n"
        "Check 'header' { $lines[0] -eq 'time,CPU Load,Note' }\n"
        "Check 'row and escaping' { $lines[1] -eq '2026-09-28 10:00:00,12%,\"a,b \"\"c\"\"\"' }\n"
        "for ($i = 0; $i -lt 30; $i++) { $logger.Log([datetime]'2026-09-28 10:00:00', (Pairs @('CPU Load', 'Note') "
        "@('12%', 'steady'))) }\n"
        "Check 'rolls over' { Test-Path (Join-Path $dir 'metrics.1.csv') }\n"
        "Check 'keeps at most 2' { @(Get-ChildItem $dir -Filter 'metrics.*.csv').Count -le 2 }\n"
        "Check 'new file has header' { (Get-Content (Join-Path $dir 'metrics.csv'))[0] -eq 'time,CPU Load,Note' }\n"
        "$logger.Log([datetime]'2026-09-28 11:00:00', (Pairs @('GPU Temp') @('55')))\n"
        "Check 'new names, new file' { (Get-Content (Join-Path $dir 'metrics.csv'))[0] -eq 'time,GPU Temp' }\n"
        "$s = NewSettings\n"
        "Check 'settings defaults' { $s.LogMetrics -eq $false -and $s.LogMaxKB -eq 1024 -and $s.LogKeepFiles -eq 5 }\n"
        ) + END,
}


def run(command, workspace, timeout):
    done = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=timeout, shell=True,
                          encoding='utf-8', errors='replace')
    return done.returncode, (done.stdout or '') + (done.stderr or '')


def check(name, workspace, reply, answers, pytest_counts=None):
    """Build, the project's own test, then the hidden tests; the harness's `tests` fields from the build and test."""
    workspace = Path(workspace)
    code, out = run(BUILD, workspace, 900)
    built = code == 0 and (workspace / 'SidebarDiagnostics' / 'bin' / 'Release' / 'SidebarDiagnostics.exe').exists()
    own = hidden_result = None
    if built:
        own_code, own_out = run('powershell.exe -NoProfile -STA -File tests\\BackgroundLayout.ps1', workspace, 300)
        own = own_code == 0  # It throws on the first failed assertion.
        with tempfile.TemporaryDirectory(prefix='sidebar-hidden-') as folder:
            script = Path(folder) / ('hidden_' + name.lower() + '.ps1')
            script.write_text(HIDDEN[name], encoding='utf-8')
            _, hidden_out = run('powershell.exe -NoProfile -STA -File "' + str(script) + '"', workspace, 300)
        found = [line for line in hidden_out.splitlines() if line.startswith('HIDDEN ')]
        hidden_result = found[-1][7:] if found else '0/?'
        failures = [line for line in hidden_out.splitlines() if line.startswith('FAIL')][:6]
    else:
        failures = [line.strip() for line in out.splitlines() if ' error ' in line][:6]
    tests = dict(ok=bool(built and own), passed=int(bool(built)) + int(bool(own)), failed=2 - int(bool(built)) -
                 int(bool(own)), summary='build ' + ('ok' if built else 'FAILED') + ', own test ' +
                 ('passed' if own else 'failed' if built else 'not run'))
    checks = {'tests pass': tests['ok'], 'builds': built, 'own test': own, 'hidden': hidden_result or '0/?'}
    if failures:
        checks['hiddenOutput'] = '\n'.join(failures)
    return tests, checks

<#
  CLI-MODE agent viewer. Opened by viewer.py when /cli view is on.

  Read-only: follows the public events file each turn writes
  (<op>.jsonl beside its <op>.txt prompt) and draws it. Nothing here reaches
  the agent, so closing this window is always safe.
#>
param(
    [Parameter(Mandatory)][string]$Folder,
    [Parameter(Mandatory)][string]$Heartbeat,
    [Parameter(Mandatory)][string]$Settings,
    [string]$Agent = 'Agent',
    [string]$Workspace = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ($PSVersionTable.PSVersion.Major -lt 7) {
    # Windows PowerShell leaves ANSI colour off in a plain console window; turn it on for this one.
    try {
        Add-Type -Namespace CliMode -Name Console -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int handle);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr handle, uint mode);
'@
        $handle = [CliMode.Console]::GetStdHandle(-11)
        $mode = [uint32]0
        if ([CliMode.Console]::GetConsoleMode($handle, [ref]$mode)) {
            [void][CliMode.Console]::SetConsoleMode($handle, $mode -bor 4)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        }
    } catch {}
}
try { $Host.UI.RawUI.WindowTitle = "CLI-MODE $GMid $Agent" } catch {}
try { [Console]::CursorVisible = $false } catch {}

$E = [char]27
$Style = @{
    Accent = '38;5;80'; Dim = '38;5;245'; Faint = '38;5;240'; Text = '38;5;252'
    Green = '38;5;114'; Red = '38;5;203'; Yellow = '38;5;221'; Blue = '38;5;111'
    Magenta = '38;5;176'; Code = '38;5;180'; Bold = '1'
}
# Glyphs by code point: this file stays ASCII, so PowerShell 5.1 reads it the same as 7.
function Glyph([int]$code) { [string][char]$code }
$GArrow = Glyph 0x25B8; $GBall = Glyph 0x25CF; $GBar = Glyph 0x2502; $GBlock = Glyph 0x258C
$GBottomLeft = Glyph 0x2570; $GBottomRight = Glyph 0x256F; $GBox = Glyph 0x2610
$GBoxTick = Glyph 0x2611; $GCorner = Glyph 0x2514; $GCross = Glyph 0x2717; $GDot = Glyph 0x2022
$GGem = Glyph 0x25C6; $GHalf = Glyph 0x25D0; $GMid = Glyph 0x00B7; $GMore = Glyph 0x2026
$GOpen = Glyph 0x25CB; $GRing = Glyph 0x25CC; $GRule = Glyph 0x2500; $GStop = Glyph 0x25A0
$GJoint = Glyph 0x253C; $GTick = Glyph 0x2713; $GTop = Glyph 0x250C; $GTopLeft = Glyph 0x256D; $GTopRight = Glyph 0x256E
$Kinds = @{
    read = @('Read', '38;5;111'); edit = @('Edit', '38;5;221'); delete = @('Delete', '38;5;203')
    move = @('Move', '38;5;221'); search = @('Search', '38;5;80'); execute = @('Run', '38;5;176')
    fetch = @('Fetch', '38;5;111'); switch_mode = @('Mode', '38;5;245'); other = @('Tool', '38;5;245')
}
$Icons = @{
    pending = @("$GOpen", '38;5;245'); in_progress = @("$GHalf", '38;5;221')
    completed = @("$GTick", '38;5;114'); failed = @("$GCross", '38;5;203')
}
$Spinner = -join (0x280B, 0x2819, 0x2839, 0x2838, 0x283C, 0x2834, 0x2826, 0x2827, 0x2807, 0x280F | ForEach-Object { [char]$_ })

function Paint([string]$code, [string]$text) { if ($code) { "$E[${code}m$text$E[0m" } else { $text } }
# Agent text is drawn in a terminal: drop its escape sequences and control characters, keep lines and tabs.
function Clean([string]$text) {
    $text = $text.Replace("`r`n", "`n") -replace "\x1b\[[0-?]*[ -/]*[@-~]", '' -replace "\x1b\][^\x07\x1b]*(\x07|\x1b\\)?", ''
    $text -replace '[\x00-\x08\x0B-\x1F\x7F]', ''
}
function Width {
    try { $columns = [Console]::WindowWidth } catch { $columns = 0 }
    if ($columns -le 0) { $columns = 100 }   # No console, e.g. output captured by a test.
    [Math]::Max(40, [Math]::Min($columns - 1, 110))
}

# ---- Output. Everything ends with a newline; the status line lives below it.
$script:statusShown = $false
$script:lastKey = $null      # toolCallId of the line just printed, for in-place updates
$script:section = $null      # 'text', 'work' or $null: a blank line separates them
function Clear-Status {
    if ($script:statusShown) { [Console]::Write("`r$E[2K"); $script:statusShown = $false }
}
function Say([string]$line = '') {
    Clear-Status
    [Console]::Write($line + "`n")
    $script:lastKey = $null
}
function Section([string]$name) {
    if ($script:section -and $script:section -ne $name) { Say }
    $script:section = $name
}

# ---- Markdown-lite: `code`, **bold**, [links](url), headings, lists, quotes, fences.
function Get-Spans([string]$text, [string]$base) {
    $parts = [regex]::Split($text, '(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\))')
    foreach ($part in $parts) {
        if (-not $part) { continue }
        if ($part.Length -ge 2 -and $part.StartsWith('`') -and $part.EndsWith('`')) {
            @{ T = $part.Substring(1, $part.Length - 2); S = $Style.Code }
        } elseif ($part.Length -ge 4 -and $part.StartsWith('**') -and $part.EndsWith('**')) {
            @{ T = $part.Substring(2, $part.Length - 4); S = ('1;' + $base).TrimEnd(';') }
        } elseif ($part -match '^\[([^\]]+)\]\(([^)\s]+)\)$') {
            @{ T = $Matches[1]; S = '4;38;5;111' }
        } else {
            @{ T = $part; S = $base }
        }
    }
}

# Word-wrap styled spans. $lead is already painted; $leadLength is its visible width.
function Write-Wrapped([object[]]$spans, [string]$lead, [int]$leadLength, [int]$indent) {
    $width = Width
    $out = [Text.StringBuilder]::new($lead)
    $col = $leadLength
    $start = $leadLength
    $space = $false
    foreach ($span in $spans) {
        $text = [string]$span.T
        if ($text -match '^\s') { $space = $true }
        $words = $text -split '\s+' | Where-Object { $_ -ne '' }
        $first = $true
        foreach ($word in $words) {
            if (-not $first) { $space = $true }
            $first = $false
            $gap = if ($space -and $col -gt $start) { 1 } else { 0 }
            if ($col + $gap + $word.Length -gt $width -and $col -gt $start) {
                [void]$out.Append("`n" + (' ' * $indent)); $col = $indent; $start = $indent; $gap = 0
            }
            if ($gap) { [void]$out.Append(' '); $col++ }
            while ($word.Length -gt $width - $col) {   # A word wider than the window: hard split.
                $room = [Math]::Max(1, $width - $col)
                [void]$out.Append((Paint $span.S $word.Substring(0, $room)) + "`n" + (' ' * $indent))
                $word = $word.Substring($room); $col = $indent; $start = $indent
            }
            [void]$out.Append((Paint $span.S $word)); $col += $word.Length
            $space = $false
        }
        if ($text -match '\s$') { $space = $true }
    }
    Say $out.ToString()
}

$script:fence = $false
$script:table = [Collections.Generic.List[string]]::new()
function Get-Plain([string]$text) { ($text -replace '\*\*', '') -replace '`', '' }
function Get-Painted([object[]]$spans) { -join ($spans | ForEach-Object { Paint $_.S $_.T }) }

# Markdown tables arrive a row at a time; draw them aligned once the table ends.
function Write-Table {
    if (-not $script:table.Count) { return }
    $rows = @()
    foreach ($line in $script:table) {
        $inner = $line.Trim()
        if ($inner.StartsWith('|')) { $inner = $inner.Substring(1) }
        if ($inner.EndsWith('|')) { $inner = $inner.Substring(0, $inner.Length - 1) }
        $cells = @($inner -split '(?<!\\)\|' | ForEach-Object { $_.Trim() })
        if (($cells -join '') -match '^[\s:-]*$') { continue }   # The |---|:--:| rule under the header.
        $rows += , $cells
    }
    $script:table.Clear()
    if (-not $rows.Count) { return }
    $count = [int]($rows | ForEach-Object { $_.Count } | Measure-Object -Maximum).Maximum
    [int[]]$widths = @(for ($i = 0; $i -lt $count; $i++) {
        ($rows | ForEach-Object { if ($i -lt $_.Count) { (Get-Plain $_[$i]).Length } else { 0 } } | Measure-Object -Maximum).Maximum
    })
    $room = (Width) - 4 - 3 * ($count - 1)
    while (($widths | Measure-Object -Sum).Sum -gt $room) {
        $widest = 0
        for ($i = 1; $i -lt $count; $i++) { if ($widths[$i] -gt $widths[$widest]) { $widest = $i } }
        if ($widths[$widest] -le 6) { break }
        $widths[$widest]--
    }
    if (($widths | Measure-Object -Sum).Sum -gt $room) {
        # Too many columns for the window: one line per row instead.
        foreach ($cells in $rows) {
            Write-Wrapped (Get-Spans ($cells -join ' | ') $Style.Text) ('    ' + (Paint $Style.Accent $GDot) + ' ') 6 6
        }
        return
    }
    $bar = Paint $Style.Faint " $GBar "
    for ($r = 0; $r -lt $rows.Count; $r++) {
        $base = if ($r -eq 0) { "1;$($Style.Text)" } else { $Style.Text }
        $out = @(for ($i = 0; $i -lt $count; $i++) {
            $cell = if ($i -lt $rows[$r].Count) { $rows[$r][$i] } else { '' }
            $plain = Get-Plain $cell
            if ($plain.Length -gt $widths[$i]) {
                Paint $base ($plain.Substring(0, $widths[$i] - 1) + $GMore)
            } else {
                (Get-Painted (Get-Spans $cell $base)) + (' ' * ($widths[$i] - $plain.Length))
            }
        })
        Say ('    ' + ($out -join $bar))
        if ($r -eq 0 -and $rows.Count -gt 1) {
            Say ('    ' + (Paint $Style.Faint ((@($widths | ForEach-Object { $GRule * $_ }) -join "$GRule$GJoint$GRule"))))
        }
    }
}

function Write-MarkdownLine([string]$line) {
    $pad = '    '
    if (-not $script:fence -and $line -match '^\s*\|.*\|\s*$') { $script:table.Add($line); return }
    Write-Table
    if ($line -match '^\s*(```|~~~)\s*(\S*)') {
        if ($script:fence) {
            Say ($pad + (Paint $Style.Faint ($GCorner + ($GRule * 23))))
        } else {
            $label = if ($Matches[2]) { ' ' + $Matches[2] + ' ' } else { '' }
            Say ($pad + (Paint $Style.Faint ("$GTop$GRule" + $label + ($GRule * [Math]::Max(2, 22 - $label.Length)))))
        }
        $script:fence = -not $script:fence
        return
    }
    if ($script:fence) {
        $room = (Width) - 6
        $text = $line.Replace("`t", '    ')
        do {
            $piece = if ($text.Length -gt $room) { $text.Substring(0, $room) } else { $text }
            Say ($pad + (Paint $Style.Faint "$GBar ") + (Paint $Style.Code $piece))
            $text = $text.Substring($piece.Length)
        } while ($text.Length)
        return
    }
    if ($line -match '^\s*$') { Say; return }
    if ($line -match '^\s*#{1,6}\s+(.*)$') {
        Write-Wrapped (Get-Spans $Matches[1] "1;$($Style.Accent)") $pad 4 4; return
    }
    if ($line -match '^\s*([-*_])(\s*\1){2,}\s*$') {
        Say ($pad + (Paint $Style.Faint ($GRule * [Math]::Min(40, (Width) - 4)))); return
    }
    if ($line -match '^\s*>\s?(.*)$') {
        Write-Wrapped (Get-Spans $Matches[1] $Style.Dim) ($pad + (Paint $Style.Faint "$GBar ")) 6 6; return
    }
    if ($line -match '^(\s*)[-*+]\s+(?:\[( |x|X)\]\s+)?(.*)$') {
        $depth = [Math]::Min(3, [int]($Matches[1].Length / 2))
        $mark = if ($Matches[2] -eq ' ') { $GBox } elseif ($Matches[2]) { $GBoxTick } else { $GDot }
        $indent = 4 + 2 * $depth
        Write-Wrapped (Get-Spans $Matches[3] $Style.Text) ((' ' * $indent) + (Paint $Style.Accent $mark) + ' ') ($indent + 2) ($indent + 2)
        return
    }
    if ($line -match '^(\s*)(\d+[.)])\s+(.*)$') {
        $depth = [Math]::Min(3, [int]($Matches[1].Length / 2))
        $indent = 4 + 2 * $depth
        $mark = $Matches[2]
        Write-Wrapped (Get-Spans $Matches[3] $Style.Text) ((' ' * $indent) + (Paint $Style.Accent $mark) + ' ') ($indent + $mark.Length + 1) ($indent + $mark.Length + 1)
        return
    }
    Write-Wrapped (Get-Spans $line $Style.Text) $pad 4 4
}

# ---- One turn per events file.
$turns = [ordered]@{}
$script:current = $null
$script:count = 0

function Start-Turn($turn) {
    $script:count++
    $script:fence = $false
    $script:section = $null
    Say
    $stamp = $turn.Started.ToString('HH:mm:ss')
    $title = "$GRule$GRule Turn $($script:count) $GMid $stamp "
    Say (Paint $Style.Faint ($title + ($GRule * [Math]::Max(3, (Width) - $title.Length))))
    if ($turn.Prompt) {
        Say ('  ' + (Paint "1;$($Style.Blue)" "$GBlock You"))
        $lines = $turn.Prompt.TrimEnd() -split "`r?`n"
        foreach ($line in ($lines | Select-Object -First 12)) {
            if ($line.Trim()) { Write-Wrapped @(@{ T = $line; S = $Style.Dim }) '    ' 4 4 } else { Say }
        }
        if ($lines.Count -gt 12) { Say ('    ' + (Paint $Style.Faint "$GMore $($lines.Count - 12) more lines")) }
        Say
    }
}

function Switch-Turn($turn) {
    if ($script:current -eq $turn) { return }
    if ($script:current) { Close-Text $script:current }
    $script:current = $turn
    if (-not $turn.Shown) { $turn.Shown = $true; Start-Turn $turn }
    elseif ($turn.Speaker) { Say; Say ('  ' + (Paint $Style.Faint "$GMore $Agent, continued")) }
}

function Open-Speaker($turn) {
    if ($turn.Speaker) { return }
    $turn.Speaker = $true
    Say ('  ' + (Paint "1;$($Style.Green)" "$GBall $Agent"))
}

function Close-Text($turn) {
    if ($turn.Partial) { $line = $turn.Partial; $turn.Partial = ''; Write-MarkdownLine $line }
    Write-Table
}

function Write-Activity($ev) {
    $kind = $Kinds[[string]$ev.kind]; if (-not $kind) { $kind = $Kinds.other }
    $icon = $Icons[[string]$ev.status]; if (-not $icon) { $icon = $Icons.pending }
    $title = Clean ([string]$ev.title)
    $where = @($ev.locations | Where-Object { $_ } | ForEach-Object {
        if ($null -ne $_.line) { "$(Clean $_.path):$($_.line)" } else { Clean ([string]$_.path) } }) -join ', '
    # ACP titles often repeat the verb and the path ("Read x.py"); keep what is new.
    if ($title -match ('^(?i)' + [regex]::Escape($kind[0]) + '\b\s*(.*)$')) { $title = $Matches[1] }
    if ($where -and $title -and $title.Contains(($where -split ':')[0])) { $where = '' }
    if ($title -and $where -and $where.Contains($title)) { $title = '' }
    if (-not $title -and -not $where) { $title = if ([string]$ev.kind -eq 'execute') { 'command' } else { 'tool call' } }
    $label = $kind[0].PadRight(7)
    $plain = "    x $label $title"
    $room = (Width) - $plain.Length - 2
    if ($where -and $room -gt 8) {
        if ($where.Length -gt $room) { $where = $GMore + $where.Substring($where.Length - $room + 1) }
    } else { $where = '' }
    if ($plain.Length -gt (Width)) { $title = $title.Substring(0, [Math]::Max(0, $title.Length - ($plain.Length - (Width)) - 1)) + $GMore }
    $line = '    ' + (Paint $icon[1] $icon[0]) + ' ' + (Paint $kind[1] $label) + ' ' +
        $(if ($title) { (Paint $Style.Text $title) + $(if ($where) { '  ' + (Paint $Style.Dim $where) }) } else { Paint $Style.Text $where })
    $id = [string]$ev.toolCallId
    if ($script:lastKey -eq $id) {
        Clear-Status
        [Console]::Write("$E[1A`r$E[2K")   # Rewrite this tool's line in place.
    }
    Say $line
    $script:lastKey = $id
}

function Write-Plan($ev) {
    $entries = @($ev.entries)
    $done = @($entries | Where-Object { $_.status -eq 'completed' }).Count
    Say ('    ' + (Paint "1;$($Style.Accent)" 'Plan') + '  ' + (Paint $Style.Dim "$done/$($entries.Count)"))
    foreach ($entry in $entries) {
        switch ([string]$entry.status) {
            'completed' { $mark = Paint $Style.Green $GTick; $s = $Style.Dim }
            'in_progress' { $mark = Paint $Style.Yellow $GArrow; $s = "1;$($Style.Text)" }
            default { $mark = Paint $Style.Faint $GOpen; $s = $Style.Text }
        }
        Write-Wrapped @(@{ T = Clean ([string]$entry.content); S = $s }) ("    $mark ") 6 6
    }
}

function Format-Usage($usage) {
    if (-not $usage) { return '' }
    $parts = @()
    if ($null -ne $usage.used) {
        $text = 'context {0:N0}' -f $usage.used
        if ($usage.size) { $text += ' / {0:N0} ({1}%)' -f $usage.size, [Math]::Round(100 * $usage.used / $usage.size) }
        $parts += $text
    }
    if ($usage.cost) { $parts += '{0:0.####} {1}' -f $usage.cost.amount, $usage.cost.currency }
    $parts -join " $GMid "
}

function Finish-Turn($turn, [string]$stop) {
    if ($turn.Finished) { return }
    Switch-Turn $turn
    Close-Text $turn
    $turn.Finished = $true
    $seconds = [int]((Get-Date) - $turn.Started).TotalSeconds
    $elapsed = if ($seconds -ge 60) { '{0}m {1:00}s' -f [int][Math]::Floor($seconds / 60), ($seconds % 60) } else { "${seconds}s" }
    switch ($stop) {
        'end_turn' { $head = Paint "1;$($Style.Green)" "$GTick Done" }
        'cancelled' { $head = Paint "1;$($Style.Yellow)" "$GStop Canceled" }
        { $_ -in 'ended', 'error' } { $head = if ($turn.Failed -or $stop -eq 'error') { Paint "1;$($Style.Red)" "$GCross Failed" } else { Paint $Style.Dim "$GStop Ended" } }
        default { $head = Paint "1;$($Style.Red)" "$GCross Stopped ($stop)" }
    }
    $tail = @("in $elapsed"); $usage = Format-Usage $turn.Usage; if ($usage) { $tail += $usage }
    Say
    Say ('  ' + $head + '  ' + (Paint $Style.Dim ($tail -join " $GMid ")))
    $script:section = $null
}

function Handle($turn, $ev) {
    $type = [string]$ev.type
    if ($type -eq 'usage') { $turn.Usage = $ev; return }
    Switch-Turn $turn
    if ($type -eq 'done') { Finish-Turn $turn ([string]$ev.stopReason); return }
    Open-Speaker $turn
    if ($type -eq 'message') {
        if ($ev.messageId -and $turn.MessageId -and $ev.messageId -ne $turn.MessageId) { Close-Text $turn; Section ''; }
        $turn.MessageId = $ev.messageId
        Section 'text'
        $text = $turn.Partial + (Clean ([string]$ev.text))
        $lines = $text -split "`n"
        $turn.Partial = $lines[-1]
        foreach ($line in ($lines | Select-Object -SkipLast 1)) { Write-MarkdownLine $line }
        $turn.Touched = Get-Date
        return
    }
    Close-Text $turn
    switch ($type) {
        'activity' { Section 'work'; Write-Activity $ev }
        'plan' { Section 'plan'; Write-Plan $ev }
        'artifact' {
            Section 'work'
            $content = $ev.content
            $name = @($content.name, $content.title, $content.uri, $content.mimeType) | Where-Object { $_ } | Select-Object -First 1
            Say ('    ' + (Paint $Style.Magenta $GGem) + ' ' + (Paint $Style.Text ("Attachment ($($content.type))")) +
                $(if ($name) { '  ' + (Paint $Style.Dim (Clean $name)) } else { '' }))
        }
        'error' {
            Section 'work'; $turn.Failed = $true
            Write-Wrapped @(@{ T = Clean ([string]$ev.message); S = $Style.Red }) ('    ' + (Paint "1;$($Style.Red)" "$GCross ")) 6 6
        }
        'context_warning' {
            Section 'work'
            Write-Wrapped @(@{ T = Clean ([string]$ev.message); S = $Style.Yellow }) ('    ' + (Paint "1;$($Style.Yellow)" '! ')) 6 6
        }
    }
}

function Read-New($turn) {
    try {
        $stream = [IO.FileStream]::new($turn.Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    } catch { return }
    try {
        if ($stream.Length -le $turn.Offset) { return }
        $stream.Position = $turn.Offset
        $buffer = [byte[]]::new($stream.Length - $turn.Offset)
        $read = $stream.Read($buffer, 0, $buffer.Length)
    } finally { $stream.Dispose() }
    if ($read -le 0) { return }
    $end = [Array]::LastIndexOf($buffer, [byte]10, $read - 1)
    if ($end -lt 0) { return }
    $turn.Offset += $end + 1
    $turn.Touched = Get-Date
    foreach ($line in [Text.Encoding]::UTF8.GetString($buffer, 0, $end + 1) -split "`n") {
        $line = $line.Trim()
        if (-not $line) { continue }
        try { $ev = $line | ConvertFrom-Json } catch { continue }
        Handle $turn $ev
    }
}

function Find-Turns([datetime]$since) {
    foreach ($file in (Get-ChildItem -LiteralPath $Folder -Filter '*.jsonl' -File -ErrorAction SilentlyContinue |
            Sort-Object CreationTime)) {
        if ($turns.Contains($file.FullName) -or $file.LastWriteTime -lt $since) { continue }
        $promptPath = [IO.Path]::ChangeExtension($file.FullName, '.txt')
        $prompt = ''
        try { $prompt = Clean ([IO.File]::ReadAllText($promptPath)) } catch {}
        if ($prompt.Length -gt 4000) { $prompt = $prompt.Substring(0, 4000) }
        $turns[$file.FullName] = [pscustomobject]@{
            Path = $file.FullName; PromptPath = $promptPath; Prompt = $prompt; Offset = 0
            Started = Get-Date; Touched = Get-Date; Shown = $false; Speaker = $false
            Partial = ''; MessageId = $null; Usage = $null; Failed = $false; Finished = $false
        }
    }
}

function Test-Enabled {
    try { return ((Get-Content -LiteralPath $Settings -Raw | ConvertFrom-Json).view -eq 'on') } catch { return $true }
}

# ---- Header, then follow turns until /cli view off or the window closes.
$width = [Math]::Min((Width) - 2, 60)
$inner = $width - 2
function Box-Line([string]$text, [string]$code) {
    $room = $inner - 2
    if ($text.Length -gt $room) { $text = $GMore + $text.Substring($text.Length - $room + 1) }
    '  ' + (Paint $Style.Faint $GBar) + ' ' + (Paint $code $text.PadRight($room)) + ' ' + (Paint $Style.Faint $GBar)
}
Say
Say ('  ' + (Paint $Style.Faint ($GTopLeft + ($GRule * $inner) + $GTopRight)))
Say (Box-Line "CLI-MODE  $GMid  agent viewer" "1;$($Style.Accent)")
Say (Box-Line $Agent "1;$($Style.Green)")
if ($Workspace) { Say (Box-Line $Workspace $Style.Dim) }
Say ('  ' + (Paint $Style.Faint ($GBottomLeft + ($GRule * $inner) + $GBottomRight)))
Say ('  ' + (Paint $Style.Dim 'Read-only. Closing this window is safe; /cli view off stops it.'))

$since = (Get-Date).AddSeconds(-15)
$tick = 0
$lastBeat = [datetime]::MinValue
try {
    while ($true) {
        if (((Get-Date) - $lastBeat).TotalMilliseconds -ge 1000) {
            $lastBeat = Get-Date
            try {
                if (-not (Test-Path -LiteralPath $Heartbeat)) { [IO.File]::WriteAllText($Heartbeat, '') }
                [IO.File]::SetLastWriteTime($Heartbeat, $lastBeat)
            } catch {}
            if (-not (Test-Enabled)) {
                Say; Say ('  ' + (Paint $Style.Dim "Viewer turned off. Closing$GMore")); Start-Sleep -Seconds 2; break
            }
            Find-Turns $since
        }
        foreach ($turn in @($turns.Values)) {
            if ($turn.Finished) { continue }
            # Show a new turn's prompt at once, unless another turn is still drawing.
            if (-not $turn.Shown -and (-not $script:current -or $script:current.Finished)) { Switch-Turn $turn }
            Read-New $turn
            $idle = ((Get-Date) - $turn.Touched).TotalSeconds
            if ($turn.Partial -and $idle -gt 3 -and $script:current -eq $turn) { Close-Text $turn }
            # The prompt file is removed when the turn settles; a quiet file without it has ended.
            if (-not $turn.Finished -and $idle -gt 2 -and -not (Test-Path -LiteralPath $turn.PromptPath)) {
                Read-New $turn
                if (-not $turn.Finished) {
                    if ($turn.Shown -or $turn.Offset) { Finish-Turn $turn 'ended' } else { $turn.Finished = $true }
                }
            }
        }
        $active = @($turns.Values | Where-Object { -not $_.Finished } | Select-Object -Last 1)
        $tick++
        if ($active.Count) {
            $seconds = [int]((Get-Date) - $active[0].Started).TotalSeconds
            $status = '  ' + (Paint $Style.Yellow $Spinner[$tick % $Spinner.Length]) + ' ' +
                (Paint $Style.Dim "$Agent is working $GMid ${seconds}s")
        } else {
            $status = '  ' + (Paint $Style.Faint "$GRing Waiting for the next turn")
        }
        [Console]::Write("`r$E[2K" + $status)
        $script:statusShown = $true
        Start-Sleep -Milliseconds 150
    }
} finally {
    Clear-Status
    try { [Console]::CursorVisible = $true } catch {}
    Remove-Item -LiteralPath $Heartbeat -ErrorAction SilentlyContinue
}

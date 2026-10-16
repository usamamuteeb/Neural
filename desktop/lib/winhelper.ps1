# Neural Desktop's Windows helper.
#
# A single long-lived Windows PowerShell 5.1 process the Electron app talks
# to over stdin/stdout: one JSON request per line in, one JSON reply per
# line out. Keeping it alive means each question ("which window is in
# front?") costs a few milliseconds instead of a ~400ms PowerShell start,
# and needs no native Node modules to compile.
#
# Commands:
#   {"id":1,"cmd":"ping"}
#   {"id":2,"cmd":"active"}                       -> foreground window: title, app, exe, pid, hwnd, bounds (physical px)
#   {"id":3,"cmd":"ocr","path":"C:\\...\\x.png"}  -> text in an image, via Windows' built-in OCR (offline)
#   {"id":4,"cmd":"search","query":"resume","limit":25} -> files from the Windows Search index
#
# Written for Windows PowerShell 5.1 (powershell.exe, present on every
# Windows 10/11 PC): the C# below sticks to C# 5 syntax, and WinRT OCR only
# works from 5.1, not PowerShell 7.

$ErrorActionPreference = 'Stop'
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class NeuralWin {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder name, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hWnd, int attribute, out RECT rect, int size);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    public static void MakeDpiAware() {
        // Per-monitor v2 (-4) so window bounds are real physical pixels on
        // every monitor, matching what Electron converts from.
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return; } catch (Exception) { }
        try { SetProcessDPIAware(); } catch (Exception) { }
    }

    public static string Title(IntPtr hWnd) {
        int n = GetWindowTextLength(hWnd);
        if (n <= 0) return "";
        StringBuilder sb = new StringBuilder(n + 2);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static string ClassOf(IntPtr hWnd) {
        StringBuilder sb = new StringBuilder(256);
        GetClassName(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static int[] Bounds(IntPtr hWnd) {
        RECT r;
        // DWMWA_EXTENDED_FRAME_BOUNDS (9): the visible frame, without the
        // invisible resize borders GetWindowRect includes on Windows 10/11.
        if (DwmGetWindowAttribute(hWnd, 9, out r, Marshal.SizeOf(typeof(RECT))) != 0) {
            if (!GetWindowRect(hWnd, out r)) return new int[] { 0, 0, 0, 0 };
        }
        return new int[] { r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top };
    }
}
"@

[NeuralWin]::MakeDpiAware()

$script:procCache = @{}

function Get-ProcInfo([uint32]$procId) {
    $key = [string]$procId
    if ($script:procCache.ContainsKey($key)) { return $script:procCache[$key] }
    $info = @{ exe = ''; name = ''; path = '' }
    try {
        $proc = [System.Diagnostics.Process]::GetProcessById([int]$procId)
        $info.exe = $proc.ProcessName + '.exe'
        $info.name = $proc.ProcessName
        try {
            $exePath = $proc.MainModule.FileName
            $info.path = $exePath
            $desc = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exePath).FileDescription
            if ($desc) { $info.name = $desc.Trim() }
        } catch {
            # Elevated or protected processes don't let us read their module
            # path -- the process name alone is still useful.
        }
    } catch {}
    if ($script:procCache.Count -gt 300) { $script:procCache.Clear() }
    $script:procCache[$key] = $info
    return $info
}

function Get-Active {
    $hWnd = [NeuralWin]::GetForegroundWindow()
    if ($hWnd -eq [IntPtr]::Zero) { return @{ hwnd = 0 } }
    [uint32]$procId = 0
    [void][NeuralWin]::GetWindowThreadProcessId($hWnd, [ref]$procId)
    $info = Get-ProcInfo $procId
    $b = [NeuralWin]::Bounds($hWnd)
    return @{
        hwnd      = $hWnd.ToInt64()
        title     = [NeuralWin]::Title($hWnd)
        cls       = [NeuralWin]::ClassOf($hWnd)
        pid       = [int]$procId
        exe       = $info.exe
        app       = $info.name
        path      = $info.path
        x         = $b[0]
        y         = $b[1]
        width     = $b[2]
        height    = $b[3]
        minimized = [NeuralWin]::IsIconic($hWnd)
    }
}

# ---- OCR (Windows.Media.Ocr, built into Windows 10/11, fully offline) ----

$script:ocrReady = $false
$script:asTaskGeneric = $null

function Initialize-Ocr {
    if ($script:ocrReady) { return }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
    $script:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
    $script:ocrReady = $true
}

function Wait-WinRt($operation, [Type]$resultType) {
    $asTask = $script:asTaskGeneric.MakeGenericMethod($resultType)
    $task = $asTask.Invoke($null, @($operation))
    [void]$task.Wait(-1)
    return $task.Result
}

function Invoke-Ocr([string]$imagePath) {
    Initialize-Ocr
    $file = Wait-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Wait-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        if ($engine -eq $null) { throw 'No OCR language pack is installed (Settings > Time & language > Language).' }
        $result = Wait-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($line in $result.Lines) { $lines += $line.Text }
        return ($lines -join "`n")
    } finally {
        try { $stream.Dispose() } catch {}
    }
}

# ---- File search (Windows Search index: instant, no crawling) ----

function Search-Index([string]$query, $limit) {
    if (-not $limit) { $limit = 25 }
    $words = @()
    foreach ($w in ($query -split '\s+')) {
        $clean = $w -replace "[%_\[\]'`"]", ''
        if ($clean) { $words += $clean }
    }
    if ($words.Count -eq 0) { return @() }
    $conds = @()
    foreach ($w in $words) { $conds += "System.FileName LIKE '%$w%'" }
    $sql = "SELECT TOP $([int]$limit) System.ItemPathDisplay, System.DateModified FROM SYSTEMINDEX " +
           "WHERE SCOPE='file:' AND " + ($conds -join ' AND ') + " ORDER BY System.DateModified DESC"
    $conn = New-Object -ComObject ADODB.Connection
    $rs = New-Object -ComObject ADODB.Recordset
    $results = @()
    try {
        $conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        $rs.Open($sql, $conn)
        while (-not $rs.EOF) {
            $itemPath = [string]$rs.Fields.Item('System.ItemPathDisplay').Value
            $modified = $rs.Fields.Item('System.DateModified').Value
            $modifiedText = ''
            if ($modified) { try { $modifiedText = ([datetime]$modified).ToString('o') } catch {} }
            if ($itemPath) { $results += @{ path = $itemPath; modified = $modifiedText } }
            $rs.MoveNext()
        }
    } finally {
        try { $rs.Close() } catch {}
        try { $conn.Close() } catch {}
    }
    return $results
}

# ---- request loop ----

$script:quit = $false
[Console]::Out.WriteLine('{"ready":true}')
[Console]::Out.Flush()

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($line -eq $null) { break }
    if (-not $line.Trim()) { continue }
    $id = $null
    try {
        $req = $line | ConvertFrom-Json
        $id = $req.id
        switch ($req.cmd) {
            'ping'   { $data = @{ pong = $true } }
            'active' { $data = Get-Active }
            'ocr'    { $data = @{ text = (Invoke-Ocr ([string]$req.path)) } }
            'search' { $data = @{ results = @(Search-Index ([string]$req.query) $req.limit) } }
            'exit'   { $script:quit = $true; $data = @{ bye = $true } }
            default  { throw "unknown command: $($req.cmd)" }
        }
        $out = @{ id = $id; ok = $true; data = $data }
    } catch {
        $out = @{ id = $id; ok = $false; error = $_.Exception.Message }
    }
    [Console]::Out.WriteLine(($out | ConvertTo-Json -Compress -Depth 6))
    [Console]::Out.Flush()
    # (A `break` inside the switch above would only leave the switch.)
    if ($script:quit) { break }
}

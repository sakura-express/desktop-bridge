$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, WindowsBase
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class SnapshotDpi {
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
}
'@
[void][SnapshotDpi]::SetThreadDpiAwarenessContext([IntPtr](-4))
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr](__HWND__))
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
# Cache properties in the same provider request rather than one cross-process call per property.
$cache = [System.Windows.Automation.CacheRequest]::new()
foreach ($property in @('Name', 'ControlType', 'BoundingRectangle', 'IsOffscreen', 'IsEnabled', 'HasKeyboardFocus', 'IsPassword')) {
    $cache.Add([System.Windows.Automation.AutomationElement]::("${property}Property"))
}
$root = $root.GetUpdatedCache($cache)
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$queue = [System.Collections.Generic.Queue[object]]::new()
$queue.Enqueue(@{node=$root; parent=$null; depth=0})
$elements = [System.Collections.Generic.List[object]]::new()
$visited = 0
$truncated = $false
while ($queue.Count -gt 0 -and $visited -lt 500 -and $clock.ElapsedMilliseconds -lt 1500) {
    $entry = $queue.Dequeue()
    $visited++
    try {
        $current = $entry.node.Cached
        $rect = $current.BoundingRectangle
        if ($current.IsOffscreen -or $rect.IsEmpty -or $rect.Width -le 0 -or $rect.Height -le 0) { continue }
        $id = 'e' + $visited
        $elements.Add(@{
            id=$id; parent=$entry.parent; role=$current.ControlType.ProgrammaticName.Replace('ControlType.', '')
            name=$(if ($current.IsPassword) { '[password]' } else { $current.Name.Substring(0, [Math]::Min(300, $current.Name.Length)) })
            enabled=$current.IsEnabled; focused=$current.HasKeyboardFocus
            bounds=@([int][Math]::Floor($rect.X), [int][Math]::Floor($rect.Y), [int][Math]::Ceiling($rect.Width), [int][Math]::Ceiling($rect.Height))
        })
        if ($entry.depth -ge 16) { $truncated=$true; continue }
        $child = $walker.GetFirstChild($entry.node, $cache)
        while ($null -ne $child) {
            if ($queue.Count -ge 500 -or $clock.ElapsedMilliseconds -ge 1500) { $truncated=$true; break }
            $queue.Enqueue(@{node=$child; parent=$id; depth=($entry.depth + 1)})
            $child = $walker.GetNextSibling($child, $cache)
        }
    } catch { $truncated=$true }
}
if ($queue.Count -gt 0) { $truncated=$true }
@{source='windows_uia'; elements=@($elements.ToArray()); truncated=$truncated} | ConvertTo-Json -Depth 6 -Compress

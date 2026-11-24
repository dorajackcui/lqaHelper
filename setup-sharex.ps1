param([string]$ArchivePath)
$ErrorActionPreference = 'Stop'
$release = '17.1.0'
$expected = 'A49D24BD924680FD6A93710FAA1564A91F4CF526B4F9C9BD64208D1613004F68'
$destination = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'vendor\ShareX'))
$archiveName = "ShareX-$release-portable.zip"
$download = !$ArchivePath
if ($download) { $ArchivePath = Join-Path ([IO.Path]::GetTempPath()) ("lqa-" + [guid]::NewGuid() + ".zip") }
try {
    if ($download) {
        Write-Host 'Downloading the official ShareX package...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/ShareX/ShareX/releases/download/v$release/$archiveName" -OutFile $ArchivePath
    }
    if ((Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash -ne $expected) {
        throw 'ShareX SHA256 did not match its official release.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $zip.Entries) {
            if (!$entry.Name -or $entry.FullName -match '^(ffmpeg\.exe|Recorder-devices-setup\.exe|exiftool\.exe|exiftool_files/.*)$') { continue }
            $target = [IO.Path]::GetFullPath([IO.Path]::Combine($destination, $entry.FullName))
            if (!$target.StartsWith($destination + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected archive entry.' }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }
    if (!(Test-Path -LiteralPath (Join-Path $destination 'ShareX.exe'))) { throw 'Missing ShareX.exe.' }
} finally {
    if ($download -and (Test-Path -LiteralPath $ArchivePath)) { Remove-Item -LiteralPath $ArchivePath }
}

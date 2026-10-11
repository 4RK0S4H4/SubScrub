<#
  extract-subs.ps1  (v2)
  Dumps every TEXT subtitle track (SRT / ASS / SSA / WebVTT) from every MKV under -Root,
  copies sidecar .srt/.ass/.ssa/.vtt files, and zips it all for upload.
  NEW in v2: writes inventory.csv listing EVERY video (mkv, mp4, m4v, mov, avi, webm, ts, m2ts)
  and EVERY subtitle track in it - including picture subs (PGS/VobSub) and tracks that were
  skipped - with the reason, so it's clear why something wasn't extracted.
  Your videos are only read, never modified.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\extract-subs.ps1 -Root "F:\" -Out "F:\subdump"
    powershell -ExecutionPolicy Bypass -File .\extract-subs.ps1 -Root "F:\" -Out "F:\subdump" -InventoryOnly
    powershell -ExecutionPolicy Bypass -File .\extract-subs.ps1 -Root "F:\" -Out "F:\subdump" -MaxPerFolder 3 -AllFrom Movies

  -Out            where to put everything (default C:\subdump)
  -MaxPerFolder   only extract from the first N videos per folder (inventory still covers all)
  -Languages      only extract these languages (default en, eng, und = untagged); -Languages * for all
  -AllFrom        folders (relative to -Root) exempt from -MaxPerFolder, e.g. -AllFrom Movies
  -InventoryOnly  just write inventory.csv (fast: no extracting) - good first step
  -NoZip          skip making the zip
  Safe to re-run: tracks already extracted are skipped.
#>
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [string]$Out = "C:\subdump",
  [int]$MaxPerFolder = 0,
  [string[]]$AllFrom = @(),
  [string[]]$Languages = @('en', 'eng', 'und'),
  [switch]$InventoryOnly,
  [switch]$NoZip
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Find-Tool($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($d in @("$env:ProgramFiles\MKVToolNix", "${env:ProgramFiles(x86)}\MKVToolNix")) {
    $p = Join-Path $d $name
    if (Test-Path -LiteralPath $p) { return $p }
  }
  throw "Can't find $name - is MKVToolNix installed?"
}
$mkvmerge   = Find-Tool 'mkvmerge.exe'
$mkvextract = Find-Tool 'mkvextract.exe'

$textCodecs = @{
  'S_TEXT/UTF8' = 'srt'; 'S_TEXT/ASCII' = 'srt'; 'S_TEXT/ASS' = 'ass'; 'S_TEXT/SSA' = 'ssa'
  'S_TEXT/WEBVTT' = 'vtt'; 'D_WEBVTT/SUBTITLES' = 'vtt'
}
$videoExt = '.mkv', '.mp4', '.m4v', '.mov', '.avi', '.webm', '.ts', '.m2ts', '.wmv'

function Clean([string]$s) { ($s -replace '[\\/:*?"<>|]', '_').Trim().TrimEnd('.', ' ') }   # Windows drops trailing dots
function CleanPath([string]$rel) { (($rel -split '\\') | ForEach-Object { Clean $_ }) -join '\' }

$scanRoot = (Resolve-Path -LiteralPath $Root).Path
$Root     = $scanRoot.TrimEnd('\')
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$outFull  = (Resolve-Path -LiteralPath $Out).Path.TrimEnd('\') + '\'
$manifest  = New-Object System.Collections.Generic.List[object]
$inventory = New-Object System.Collections.Generic.List[object]

Write-Host "Scanning $scanRoot ..."
$scanErrors = @()
$all = Get-ChildItem -LiteralPath $scanRoot -Recurse -File -ErrorAction SilentlyContinue -ErrorVariable +scanErrors |
  Where-Object { -not $_.FullName.StartsWith($outFull, [StringComparison]::OrdinalIgnoreCase) }
$videos   = @($all | Where-Object { $videoExt -contains $_.Extension.ToLower() })
$sidecars = @($all | Where-Object { $_.Extension.ToLower() -in '.srt', '.ass', '.ssa', '.vtt', '.sup', '.sub', '.idx' })
$byExt = $videos | Group-Object { $_.Extension.ToLower() } | ForEach-Object { "$($_.Count) $($_.Name)" }
Write-Host "Found $($videos.Count) videos ($($byExt -join ', ')), $($sidecars.Count) sidecar subtitle files."
if ($scanErrors.Count) { Write-Warning "$($scanErrors.Count) folders/files couldn't be read (permissions or very long paths) - see scan-errors.txt" }

# which videos to actually extract from
$extractSet = @{}
$mkvs = @($videos | Where-Object { $_.Extension -ieq '.mkv' })
$allPrefixes = @($AllFrom | ForEach-Object { (Join-Path $Root $_.Trim('\')).TrimEnd('\') + '\' })
function In-AllFrom($path) {
  foreach ($pre in $allPrefixes) { if ($path.StartsWith($pre, [StringComparison]::OrdinalIgnoreCase)) { return $true } }
  return $false
}
foreach ($pre in $allPrefixes) { if (-not (Test-Path -LiteralPath $pre)) { Write-Warning "-AllFrom folder not found: $pre" } }
if ($MaxPerFolder -gt 0) {
  $mkvs | Where-Object { In-AllFrom $_.FullName } | ForEach-Object { $extractSet[$_.FullName] = $true }
  $mkvs | Where-Object { -not (In-AllFrom $_.FullName) } | Group-Object DirectoryName | ForEach-Object {
    $_.Group | Sort-Object Name | Select-Object -First $MaxPerFolder | ForEach-Object { $extractSet[$_.FullName] = $true } }
} else { $mkvs | ForEach-Object { $extractSet[$_.FullName] = $true } }
Write-Host "Extracting from $($extractSet.Count) of $($mkvs.Count) MKVs."

$i = 0
foreach ($v in $videos) {
  $i++
  $rel = $v.FullName.Substring($Root.Length).TrimStart('\')
  Write-Progress -Activity "Reading videos" -Status "$i / $($videos.Count): $rel" -PercentComplete (100 * $i / [Math]::Max($videos.Count, 1))
  $sizeGB = [Math]::Round($v.Length / 1GB, 2)

  $json = & $mkvmerge -J $v.FullName 2>$null | Out-String
  try { $info = $json | ConvertFrom-Json } catch { $info = $null }
  if (-not $info -or -not $info.container.recognized) {
    $inventory.Add([pscustomobject]@{ Video = $rel; Ext = $v.Extension; SizeGB = $sizeGB; Track = ''; Codec = ''
      Language = ''; Name = ''; SDH = ''; Forced = ''; Default = ''; Status = "unreadable by mkvmerge (path length $($v.FullName.Length))" })
    continue
  }
  $allSubs = @($info.tracks | Where-Object { $_.type -eq 'subtitles' })
  if ($allSubs.Count -eq 0) {
    $inventory.Add([pscustomobject]@{ Video = $rel; Ext = $v.Extension; SizeGB = $sizeGB; Track = ''; Codec = ''
      Language = ''; Name = ''; SDH = ''; Forced = ''; Default = ''; Status = 'no subtitle tracks' })
    continue
  }

  $isMkv = $v.Extension -ieq '.mkv'
  $doExtract = $isMkv -and $extractSet.ContainsKey($v.FullName) -and -not $InventoryOnly
  $destDir = Join-Path $Out (CleanPath ([IO.Path]::ChangeExtension($rel, $null)))
  $specs = @()
  foreach ($t in $allSubs) {
    $p     = $t.properties
    $codec = [string]$p.codec_id
    if (-not $codec) { $codec = [string]$t.codec }
    $lang  = if ($p.language_ietf) { $p.language_ietf } elseif ($p.language) { $p.language } else { 'und' }
    $name  = [string]$p.track_name
    $sdh   = [bool]$p.flag_hearing_impaired -or ($name -match '\b(SDH|CC|HI)\b|hearing|closed.?caption')
    $isText = $textCodecs.ContainsKey($codec)
    $base   = ($lang -split '-')[0].ToLower()
    $langOk = ($Languages -contains '*') -or ($Languages -contains $base) -or ($Languages -contains $lang.ToLower())

    $status =
      if (-not $isText)      { 'skipped: picture-based (' + $t.codec + ')' }
      elseif (-not $langOk)  { 'skipped: language ' + $lang }
      elseif (-not $isMkv)   { 'skipped: text sub inside ' + $v.Extension + ' (not MKV)' }
      elseif ($InventoryOnly){ 'text (inventory only)' }
      elseif (-not $doExtract) { 'skipped: -MaxPerFolder limit' }
      else { 'extracted' }

    if ($isText -and $langOk -and $doExtract) {
      $tags = @("t$($t.id)", $lang)
      if ($sdh)             { $tags += 'SDH' }
      if ($p.forced_track)  { $tags += 'forced' }
      if ($p.default_track) { $tags += 'default' }
      if ($name) { $n = Clean $name; if ($n.Length -gt 40) { $n = $n.Substring(0, 40) }; $tags += $n }
      $file = (($tags -join '.') -replace '\s+', '_') + '.' + $textCodecs[$codec]
      $dest = Join-Path $destDir $file
      if (-not (Test-Path -LiteralPath $dest)) { $specs += "$($t.id):$dest" }
      $manifest.Add([pscustomobject]@{
        Video = $rel; Track = $t.id; Codec = $codec; Language = $lang; Name = $name
        SDH = $sdh; Forced = [bool]$p.forced_track; Default = [bool]$p.default_track
        File = $dest.Substring($Out.Length).TrimStart('\')
      })
    }
    $inventory.Add([pscustomobject]@{ Video = $rel; Ext = $v.Extension; SizeGB = $sizeGB; Track = $t.id; Codec = $t.codec
      Language = $lang; Name = $name; SDH = $sdh; Forced = [bool]$p.forced_track; Default = [bool]$p.default_track; Status = $status })
  }

  if ($specs.Count -gt 0) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    & $mkvextract $v.FullName tracks @specs | Out-Null
    if ($LASTEXITCODE -gt 1) { Write-Warning "mkvextract had trouble with $rel" }
  }
}
Write-Progress -Activity "Reading videos" -Completed

if (-not $InventoryOnly) {
  foreach ($s in $sidecars) {
    $rel  = $s.FullName.Substring($Root.Length).TrimStart('\')
    $dest = Join-Path $Out ('_sidecars\' + (CleanPath $rel))
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $s.FullName -Destination $dest -Force
    $manifest.Add([pscustomobject]@{
      Video = $rel; Track = 'sidecar'; Codec = $s.Extension; Language = ''; Name = $s.Name
      SDH = ($s.Name -match '\.(sdh|hi|cc)\.'); Forced = ($s.Name -match '\.forced\.'); Default = $false
      File = $dest.Substring($Out.Length).TrimStart('\')
    })
  }
  $manifest | Export-Csv -LiteralPath (Join-Path $Out 'manifest.csv') -NoTypeInformation -Encoding UTF8
}
$inventory | Export-Csv -LiteralPath (Join-Path $Out 'inventory.csv') -NoTypeInformation -Encoding UTF8
if ($scanErrors.Count) { $scanErrors | ForEach-Object { $_.TargetObject } | Set-Content -LiteralPath (Join-Path $Out 'scan-errors.txt') -Encoding UTF8 }

# --- summary
$withText = @($inventory | Where-Object { $_.Status -eq 'extracted' -or $_.Status -like 'text*' } | Select-Object -ExpandProperty Video -Unique).Count
$picOnly  = @($inventory | Group-Object Video | Where-Object {
  ($_.Group | Where-Object { $_.Status -like 'skipped: picture*' }) -and -not ($_.Group | Where-Object { $_.Status -eq 'extracted' -or $_.Status -like 'text*' -or $_.Status -like '*not MKV*' -or $_.Status -like '*MaxPerFolder*' }) }).Count
$noSubs   = @($inventory | Where-Object { $_.Status -eq 'no subtitle tracks' }).Count
$notMkv   = @($inventory | Where-Object { $_.Status -like '*not MKV*' } | Select-Object -ExpandProperty Video -Unique).Count
$bad      = @($inventory | Where-Object { $_.Status -like 'unreadable*' }).Count
Write-Host ""
Write-Host "Videos: $($videos.Count)"
Write-Host "  with text subtitles:            $withText"
Write-Host "  picture subtitles only (PGS..): $picOnly"
Write-Host "  text subs but not MKV:          $notMkv"
Write-Host "  no subtitles at all:            $noSubs"
Write-Host "  unreadable:                     $bad"
Write-Host "Details in $(Join-Path $Out 'inventory.csv')"

if (-not $NoZip) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = "$Out.zip"
  if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
  [IO.Compression.ZipFile]::CreateFromDirectory($Out, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
  $mb = [Math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
  Write-Host "Zipped to $zip ($mb MB) - attach that to the chat."
}

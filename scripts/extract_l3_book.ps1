# Extract one book (PDF or corpus-raw .txt) into knowledge/scm-books-rebuild/<slug>/graphify-out/
param(
    [Parameter(Mandatory)][string]$Name
)
$ErrorActionPreference = "Stop"
$Repo = if (Test-Path "$PSScriptRoot\..\scm_agent") { "$PSScriptRoot\.." } else { $PSScriptRoot }
$Corpus = "C:\Users\Gamer\Documents\scm-books-corpus"
$Raw = Join-Path $Repo "knowledge\scm-books-rebuild\corpus-raw"
$Out = Join-Path $Repo "knowledge\scm-books-rebuild\$([IO.Path]::GetFileNameWithoutExtension($Name))"
$Graph = Join-Path $Out "graphify-out\graph.json"

$envFile = Join-Path $Repo ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*MOONSHOT_API_KEY=(.+)$') { $env:MOONSHOT_API_KEY = $matches[1].Trim().Trim('"') }
    }
}

if (Test-Path $Graph) { Write-Host "skip $Name"; exit 0 }

$stem = [IO.Path]::GetFileNameWithoutExtension($Name)
$work = Join-Path $Repo "knowledge\scm-books-rebuild\_work-$stem"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$work\raw" | Out-Null

$txt = Join-Path $Raw "$stem.txt"
$pdf = Join-Path $Corpus $Name
if (-not $Name.EndsWith('.pdf')) { $pdf = Join-Path $Corpus "$stem.pdf" }

if (Test-Path $txt) {
    Copy-Item $txt "$work\raw\$stem.txt"
} elseif (Test-Path $pdf) {
    Copy-Item $pdf "$work\raw\$([IO.Path]::GetFileName($pdf))"
} else {
    Write-Error "No source for $stem"
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null
graphify extract $work --backend kimi --max-concurrency 1 --token-budget 60000 --out $Out
if (-not (Test-Path $Graph)) {
    Write-Warning "extract failed: $Name"
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "ok $Name -> $Graph"

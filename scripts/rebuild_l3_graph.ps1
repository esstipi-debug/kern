# Re-extract priority SCM books with graphify (Kimi) and merge into knowledge/scm-books/.
# Usage: .\scripts\rebuild_l3_graph.ps1 [-Batch 1|2|all] [-MergeOnly]
param(
    [ValidateSet("1", "2", "all")]
    [string]$Batch = "all",
    [switch]$MergeOnly
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$Repo\scm_agent")) { $Repo = Split-Path $PSScriptRoot -Parent }
$Corpus = "C:\Users\Gamer\Documents\scm-books-corpus"
$Rebuild = Join-Path $Repo "knowledge\scm-books-rebuild"
$Committed = Join-Path $Repo "knowledge\scm-books\graph.json"

$Batch1 = @(
    "vandeput-inventory-optimization-models-simulations.pdf",
    "hyndman-forecasting-principles-practice-2ed.pdf",
    "palamariu-alicke-from-source-to-sold.pdf"
)
$Batch2 = @(
    "chopra-meindl-supply-chain-management.pdf",
    "christopher-logistics-supply-chain-management.pdf",
    "grant-sustainable-logistics-supply-chain.pdf",
    "ivanov-global-supply-chain-operations.pdf"
)

$pick = switch ($Batch) {
    "1" { $Batch1 }
    "2" { $Batch2 }
    default { $Batch1 + $Batch2 }
}

# Load MOONSHOT_API_KEY from repo .env if present
$envFile = Join-Path $Repo ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            $k = $matches[1].Trim(); $v = $matches[2].Trim().Trim('"')
            if ($k -eq "MOONSHOT_API_KEY" -and $v) { $env:MOONSHOT_API_KEY = $v }
        }
    }
}
if (-not $env:MOONSHOT_API_KEY) {
    Write-Error "MOONSHOT_API_KEY not set. Add it to $envFile or the environment."
}

function Extract-Book($pdfName) {
    $pdf = Join-Path $Corpus $pdfName
    if (-not (Test-Path $pdf)) { Write-Warning "Missing: $pdf"; return $null }
    $slug = [IO.Path]::GetFileNameWithoutExtension($pdfName)
    $out = Join-Path $Rebuild $slug
    $graph = Join-Path $out "graphify-out\graph.json"
    if (Test-Path $graph) {
        Write-Host "[skip] $slug (graph exists)"
        return $graph
    }
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    Write-Host "[extract] $pdfName -> $out"
    graphify extract $pdf --backend kimi --max-concurrency 1 --token-budget 80000 --out $out
    if (-not (Test-Path $graph)) { Write-Warning "No graph produced for $slug"; return $null }
    return $graph
}

if (-not $MergeOnly) {
    foreach ($name in $pick) { Extract-Book $name | Out-Null }
}

$graphs = @($Committed)
Get-ChildItem $Rebuild -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $g = Join-Path $_.FullName "graphify-out\graph.json"
    if (Test-Path $g) { $graphs += $g }
}
$merged = Join-Path $Repo "knowledge\scm-books\graph-new.json"
Write-Host "[merge] $($graphs.Count) graphs -> $merged"
graphify merge-graphs @graphs --out $merged

# Stats
python -c @"
import json
from pathlib import Path
p = Path(r'$merged')
g = json.loads(p.read_text(encoding='utf-8'))
links = g.get('links', [])
ex = sum(1 for e in links if e.get('confidence') == 'EXTRACTED')
inf = sum(1 for e in links if e.get('confidence') == 'INFERRED')
print(f'nodes={len(g[\"nodes\"])} edges={len(links)} EXTRACTED={ex} ({100*ex/len(links):.1f}%) INFERRED={inf}')
"@

Write-Host "Review graph-new.json then: Move-Item -Force $merged $Committed"

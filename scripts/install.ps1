# Grasp installer (Windows). Idempotent. Prints "GRASP INSTALL OK" or "FAILED: <reason>".
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "FAILED: $msg" -ForegroundColor Red; exit 1 }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Write-Host "Grasp repo: $RepoRoot"

function Get-Python {
    foreach ($cand in @("py -3", "python", "python3")) {
        $exe, $arg = $cand.Split(" ", 2)
        try {
            $v = & $exe $arg --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 10) {
                    $full = & $exe $arg -c "import sys; print(sys.executable)" 2>$null
                    if ($full) { return $full.Trim() }
                }
            }
        } catch {}
    }
    return $null
}

$Py = Get-Python
if (-not $Py) { Fail "no Python 3.10+ found. Install Python 3.10+ from python.org and re-run." }
Write-Host "Using Python: $Py"

Write-Host "Installing pip dependencies..."
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $RepoRoot "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Write-Host "Smoke test (import + scale tests)..."
$env:PYTHONPATH = $RepoRoot
& $Py -c "import grasp, grasp.server; from grasp import Computer; print('import OK')"
if ($LASTEXITCODE -ne 0) { Fail "grasp package failed to import" }
& $Py -m pytest -q (Join-Path $RepoRoot "tests")
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: some tests failed (install still usable)" -ForegroundColor Yellow }

$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "Registering 'grasp' MCP server with Claude Code..."
    try { & claude mcp remove grasp -s user 2>$null } catch {}
    # pass "--" via a variable: a literal -- is eaten by PowerShell and never reaches claude
    $sep = "--"
    & claude mcp add grasp -s user -e "PYTHONPATH=$RepoRoot" $sep "$Py" -m grasp
    if ($LASTEXITCODE -ne 0) { Fail "claude mcp add failed" }
    Write-Host ""
    Write-Host "GRASP INSTALL OK" -ForegroundColor Green
    Write-Host "Restart Claude Code, then ask it to 'take a screenshot of my screen with Grasp'."
} else {
    Write-Host ""
    Write-Host "GRASP INSTALL OK (Python side)" -ForegroundColor Green
    Write-Host "Claude Code CLI ('claude') not found on PATH. Add this to your MCP config:" -ForegroundColor Yellow
    Write-Host "  `"grasp`": { `"command`": `"$($Py -replace '\\','\\')`", `"args`": [`"-m`",`"grasp`"], `"env`": { `"PYTHONPATH`": `"$($RepoRoot -replace '\\','\\')`" } }"
}

<#
.SYNOPSIS
  Windows task runner. The same targets as the Makefile, for machines without
  `make`.

.DESCRIPTION
  Every command here is a thin wrapper over `python -m bench`. If something
  goes wrong, run the underlying command directly -- the wrapper adds nothing
  except typing convenience.

.EXAMPLE
  .\run.ps1 install
  .\run.ps1 dataset
  .\run.ps1 doctor
  .\run.ps1 bench
  .\run.ps1 report

.NOTES
  If PowerShell refuses to run this file, it is the execution policy, not the
  script. Either allow local scripts for your user once:

      Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

  or bypass it for a single invocation:

      powershell -ExecutionPolicy Bypass -File .\run.ps1 doctor
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'install', 'dataset', 'doctor', 'bench', 'report',
                 'selftest', 'test', 'up', 'down', 'clean')]
    [string]$Task = 'help',

    # Extra arguments passed straight through, e.g. .\run.ps1 bench -Extra '-v'
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Platforms = @('cognodb', 'neo4j_aura', 'memgraph', 'falkordb', 'arangodb')
$Compose = @('--env-file', '.env', '-f', 'docker/docker-compose.yml')

function Get-Python {
    <#
      Prefer the project venv. Falling back to the system interpreter is a
      convenience, but it means the pinned dependency versions are not
      guaranteed -- which is exactly the kind of thing that makes two runs
      incomparable, so we say so out loud.
    #>
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    Write-Warning "No .venv found - using the system Python. Run '.\run.ps1 install' for pinned dependencies."
    return 'python'
}

function Invoke-Bench {
    param([string[]]$BenchArgs)
    $py = Get-Python
    Write-Host "> $py -m bench $($BenchArgs -join ' ')" -ForegroundColor DarkGray
    & $py -m bench @BenchArgs
    if ($LASTEXITCODE -ne 0) { throw "bench exited with code $LASTEXITCODE" }
}

switch ($Task) {

    'help' {
        Write-Host ""
        Write-Host "  Graph database benchmark - Windows task runner" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  .\run.ps1 install    create .venv and install pinned dependencies"
        Write-Host "  .\run.ps1 selftest   exercise the harness offline (no accounts needed)"
        Write-Host "  .\run.ps1 test       run the unit tests"
        Write-Host "  .\run.ps1 up         start the three capped self-hosted databases"
        Write-Host "  .\run.ps1 dataset    download, sample and freeze the dataset"
        Write-Host "  .\run.ps1 doctor     check credentials and connectivity"
        Write-Host "  .\run.ps1 bench      run the full benchmark on every platform"
        Write-Host "  .\run.ps1 report     regenerate RESULTS.md and the charts"
        Write-Host "  .\run.ps1 down       stop the self-hosted databases"
        Write-Host "  .\run.ps1 clean      delete generated results"
        Write-Host ""
        Write-Host "  Typical first run, in order:" -ForegroundColor Cyan
        Write-Host "    install -> selftest -> up -> dataset -> doctor -> bench -> report"
        Write-Host ""
    }

    'install' {
        python -m venv .venv
        & .\.venv\Scripts\python.exe -m pip install --upgrade pip
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
        if (-not (Test-Path .env)) {
            Copy-Item .env.example .env
            Write-Host "Created .env from the template - open it and fill in your credentials." -ForegroundColor Yellow
        }
        Write-Host "Done. Next: .\run.ps1 selftest" -ForegroundColor Green
    }

    'up' {
        if (-not (Test-Path .env)) { throw "No .env file. Run '.\run.ps1 install' first, then fill it in." }
        docker compose @Compose up -d
        # PowerShell does not treat a non-zero exit from a native command as a
        # terminating error, so without this check the script would sail past a
        # failed compose and print the "caps confirmed" message over an empty
        # docker stats table -- which reads exactly like success.
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "docker compose failed - nothing was started." -ForegroundColor Red
            Write-Host "Most common cause: a value is missing from .env (ARANGO_PASSWORD cannot be blank)." -ForegroundColor Yellow
            exit 1
        }
        Write-Host ""
        Write-Host "Confirming the resource caps actually applied:" -ForegroundColor Cyan
        Start-Sleep -Seconds 5
        docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
        Write-Host ""
        Write-Host "MEM USAGE must show a 256MiB limit on all three. If it shows your" -ForegroundColor Yellow
        Write-Host "full host RAM instead, the cap did not apply and the numbers are void." -ForegroundColor Yellow
    }

    'down'     { docker compose @Compose down }
    'dataset'  { Invoke-Bench (@('dataset') + $Extra) }
    'doctor'   { Invoke-Bench (@('doctor') + $Extra) }
    'report'   { Invoke-Bench (@('report') + $Extra) }

    'bench' {
        $args = @('run')
        foreach ($p in $Platforms) { $args += @('--platform', $p) }
        Invoke-Bench ($args + '-v' + $Extra)
    }

    'selftest' {
        Invoke-Bench @('dataset', '--fixture')
        Invoke-Bench @('selftest')
    }

    'test' {
        $py = Get-Python
        & $py -m pytest tests -q
    }

    'clean' {
        Remove-Item -ErrorAction SilentlyContinue results\raw\*.json
        Remove-Item -ErrorAction SilentlyContinue results\charts\*.png
        Remove-Item -ErrorAction SilentlyContinue results\shared_inputs.json
        Remove-Item -ErrorAction SilentlyContinue RESULTS.md
        Write-Host "Generated results removed (the dataset in data\ was kept)." -ForegroundColor Green
    }
}
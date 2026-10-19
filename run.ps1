param([Parameter(ValueFromRemainingArguments=$true)][string[]]$PdfFiles)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonExe)) {
    Write-Error 'Zuerst: py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install .'
    exit 1
}
& $PythonExe (Join-Path $ProjectRoot 'run-local.py') @PdfFiles
exit $LASTEXITCODE

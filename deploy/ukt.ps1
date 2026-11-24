# Run deploy/ukt.sh from PowerShell with Git Bash and the Doppler secrets.
#
#   .\deploy\ukt.ps1 status
#   .\deploy\ukt.ps1 deploy sha-1a2b3c4
#   .\deploy\ukt.ps1 stop
#
# Plain `bash` in PowerShell is WSL's bash.exe (C:\Windows\System32), which
# fails without a Linux distro. This finds Git for Windows' bash instead.
# Needs the university network (campus Wi-Fi or VPN). Usually you don't need
# this at all: Actions -> "Operate the cluster" does the same from GitHub.

$ErrorActionPreference = 'Stop'

$candidates = @(
    (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')
)
$gitBash = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $gitBash) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $guess = Join-Path (Split-Path (Split-Path $git.Source)) 'bin\bash.exe'
        if (Test-Path $guess) { $gitBash = $guess }
    }
}
if (-not $gitBash) { throw 'Git Bash not found. Install Git for Windows: winget install Git.Git' }
if (-not (Get-Command doppler -ErrorAction SilentlyContinue)) {
    throw 'Doppler CLI not found. Install it: winget install doppler.doppler'
}

$script = Join-Path $PSScriptRoot 'ukt.sh'
& doppler run -- $gitBash $script @args
exit $LASTEXITCODE

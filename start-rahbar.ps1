# Start the whole RAHBAR stack on this laptop with ONE command, and open it in Edge.
#
#   powershell -ExecutionPolicy Bypass -File start-rahbar.ps1          # start and open
#   powershell -ExecutionPolicy Bypass -File start-rahbar.ps1 -Stop    # stop minikube and the port-forward
#
# It never deletes anything: it starts Docker Desktop and Ollama if they are not running,
# starts the EXISTING minikube cluster (data kept), puts the default kubectl context back
# (minikube start switches it), waits for the pods, opens the Ingress on 127.0.0.1:18081,
# and opens Edge with rahbar.local mapped to this PC (no hosts-file edit, no admin rights).
param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$MK = "$env:LOCALAPPDATA\Programs\minikube\minikube.exe"
$Edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$Default = (kubectl config current-context)

function Step($t) { Write-Host "`n== $t" -ForegroundColor White }

if ($Stop) {
    Step "Stopping the port-forward and minikube (the cluster's data is kept)"
    Get-CimInstance Win32_Process -Filter "Name = 'kubectl.exe'" | Where-Object { $_.CommandLine -match "port-forward svc/ingress-nginx-controller 18081" } | ForEach-Object { Stop-Process -Id $_.ProcessId }
    & $MK stop
    if ((kubectl config current-context) -ne $Default) { kubectl config use-context $Default | Out-Null }
    exit 0
}

Step "Free memory (minikube needs about 4 GB, Ollama about 3 GB)"
$free = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
Write-Host "free RAM: $free MB"
if ($free -lt 1500) { Write-Host "Too little free memory: close Chrome, VS Code and other heavy apps first, then run this again." ; exit 1 }
if ($free -lt 3000) { Write-Host "Low memory: it will work, but the first answer can take a minute or two." }

Step "Docker Desktop"
$DockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# "cmd /c ... 2>nul" keeps docker's "not running" message from stopping this script.
function Docker-Up { cmd /c "docker version --format {{.Server.Version}} >nul 2>nul"; return ($LASTEXITCODE -eq 0) }
function Wait-Docker($seconds) {
    for ($i = 0; $i -lt ($seconds / 5); $i++) { Start-Sleep 5; if (Docker-Up) { return $true } }
    return $false
}
if (-not (Docker-Up)) {
    Start-Process $DockerExe
    if (-not (Wait-Docker 90)) {
        # Known Docker Desktop 4.91 bug: a stale socket file (sailor-ingest.sock) blocks start-up.
        # Fix: quit Docker, RENAME (never delete) the two socket folders, start Docker again.
        Write-Host "Docker did not start; applying the stale-socket fix (renaming 2 folders, nothing deleted)"
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq "Docker Desktop" -or $_.Name -like "com.docker*" } | Stop-Process -Force
        Start-Sleep 6
        $ts = Get-Date -Format "yyyyMMdd-HHmmss"
        foreach ($dir in "$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine") {
            if (Test-Path $dir) { Rename-Item $dir "$(Split-Path $dir -Leaf).stale-$ts"; Write-Host "renamed $dir -> .stale-$ts" }
        }
        Start-Process $DockerExe
        if (-not (Wait-Docker 180)) { Write-Host "Docker still did not start. Open Docker Desktop to see its error, then run this again."; exit 1 }
    }
}
Write-Host "Docker is running"

Step "Ollama (nomic-embed-text and llama3.2:3b)"
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null } catch {
    Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"; Start-Sleep 8
}
(Invoke-RestMethod http://127.0.0.1:11434/api/tags).models.name -join ", "

Step "minikube (existing cluster, Calico)"
& $MK start --driver=docker --cni=calico --memory=4096 --cpus=4 --keep-context=true
if ((kubectl config current-context) -ne $Default) { kubectl config use-context $Default | Out-Null; Write-Host "default kubectl context restored to $Default" }

function Wait-Ready($ns, $selector, $count) {
    # Right after minikube boots, Deployment status is stale and pods show "Unknown": count
    # the containers that are really ready instead.
    for ($i = 0; $i -lt 72; $i++) {
        $states = kubectl --context minikube -n $ns get pods -l $selector -o "jsonpath={range .items[*]}{.status.containerStatuses[0].ready}{' '}{end}" 2>$null
        $ready = @(($states -split ' ') | Where-Object { $_ -eq 'true' }).Count
        if ($ready -ge $count) { Write-Host "$selector in ${ns}: $ready ready"; return }
        Start-Sleep 5
    }
    throw "$selector in $ns did not become ready within 6 minutes (try: kubectl --context minikube -n $ns get pods)"
}

Step "Waiting for the pods to be really Ready"
Wait-Ready "ingress-nginx" "app.kubernetes.io/component=controller" 1
Wait-Ready "rahbar" "app=chroma" 1
Wait-Ready "rahbar" "app=redis" 1
Wait-Ready "rahbar" "app=api" 3
Wait-Ready "rahbar" "app=ui" 1
kubectl --context minikube -n rahbar get pods

Step "Opening the Ingress on 127.0.0.1:18081"
$listening = Get-NetTCPConnection -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process kubectl -ArgumentList "--context minikube -n ingress-nginx port-forward svc/ingress-nginx-controller 18081:80" -WindowStyle Minimized
}
$code = 0
for ($i = 0; $i -lt 20 -and $code -ne 200; $i++) {
    Start-Sleep 3
    try { $code = (Invoke-WebRequest http://127.0.0.1:18081/ -Headers @{ Host = "rahbar.local" } -UseBasicParsing -TimeoutSec 5).StatusCode } catch { $code = 0 }
}
if ($code -ne 200) { Write-Host "The Ingress did not answer on 127.0.0.1:18081." ; exit 1 }
Write-Host "RAHBAR answers through the Ingress: HTTP $code"

if (-not $NoBrowser) {
    Step "Opening http://rahbar.local:18081 in Edge"
    Start-Process $Edge -ArgumentList "--user-data-dir=`"$env:TEMP\rahbar-demo`"", "--host-resolver-rules=`"MAP rahbar.local 127.0.0.1`"", "http://rahbar.local:18081/"
}
Write-Host "`nDone. To stop later: powershell -ExecutionPolicy Bypass -File start-rahbar.ps1 -Stop"

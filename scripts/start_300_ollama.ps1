param(
    [Parameter(Mandatory=$true)][ValidateSet('cuda_v13','vulkan')][string]$Backend,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$taskOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
if (Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction SilentlyContinue) {
    throw 'Port 11435 is already in use. Inspect the owner before proceeding.'
}
$taskOllama = 'C:\Users\myori\AppData\Local\Programs\Ollama\ollama.exe'
$taskStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$taskPrefix = Join-Path $taskOutput "server_${Backend}_${taskStamp}"
foreach ($taskSuffix in @('.stdout.log','.stderr.log','.json')) {
    if (Test-Path -LiteralPath ($taskPrefix + $taskSuffix)) { throw 'Server record exists; refusing overwrite.' }
}
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_FLASH_ATTENTION = 'false'
$env:GGML_CUDA_DISABLE_GRAPHS = '1'
$env:OLLAMA_LLM_LIBRARY = $Backend
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NO_CLOUD = '1'
if ($Backend -eq 'cuda_v13') {
    $env:CUDA_VISIBLE_DEVICES = '0'
    $env:OLLAMA_VULKAN = '0'
    $env:GGML_VK_VISIBLE_DEVICES = '-1'
} else {
    $env:CUDA_VISIBLE_DEVICES = '-1'
    $env:OLLAMA_VULKAN = '1'
    $env:GGML_VK_VISIBLE_DEVICES = '0'
}
$taskServer = Start-Process -FilePath $taskOllama -ArgumentList 'serve' -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput ($taskPrefix + '.stdout.log') -RedirectStandardError ($taskPrefix + '.stderr.log')
$taskRecord = [ordered]@{
    pid=$taskServer.Id; backend=$Backend; started_at=(Get-Date -Format o);
    host='127.0.0.1:11435'; flash_attention=$false; cuda_graphs=$false;
    max_loaded_models=1; parallel=1; cloud=$false;
    stdout=($taskPrefix + '.stdout.log'); stderr=($taskPrefix + '.stderr.log');
    executable=$taskOllama
}
$taskRecord | ConvertTo-Json | Set-Content -LiteralPath ($taskPrefix + '.json') -Encoding UTF8
$taskRecord | ConvertTo-Json -Compress

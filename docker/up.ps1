param([string]$Email='admin@example.com',[string]$Origin='http://127.0.0.1:8787')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
  docker compose version
  if($LASTEXITCODE -ne 0){throw 'Docker Compose is required'}
  if(-not (Test-Path -LiteralPath '.env.docker')) {
    $taskProject=if($env:COMPOSE_PROJECT_NAME){$env:COMPOSE_PROJECT_NAME}else{'fishbot-saas'}
    $taskContainers=@(docker ps -aq --filter "label=com.docker.compose.project=$taskProject")
    if($LASTEXITCODE -ne 0){throw 'Cannot inspect existing deployments'}
    $taskVolumes=@(docker volume ls -q --filter "label=com.docker.compose.project=$taskProject")
    if($LASTEXITCODE -ne 0){throw 'Cannot inspect existing deployment volumes'}
    $taskVolumeNames=@(docker volume ls --format '{{.Name}}')
    if($LASTEXITCODE -ne 0){throw 'Cannot inspect existing data volumes'}
    if($taskContainers.Count -or $taskVolumes.Count -or $taskVolumeNames -contains "${taskProject}_fishbot-data") {
      throw 'Existing deployment or data volume found; restore the original deployment directory/configuration instead of initializing a new one.'
    }
  }
  docker run --rm --user 0:0 --entrypoint node -v "${taskRoot}:/export" fishbot/qq-ai-saas:0.1.0 scripts/docker-init.mjs /export $Email $Origin
  if($LASTEXITCODE -ne 0){throw 'Deployment configuration initialization failed'}
  docker compose --env-file .env.docker up -d
  if($LASTEXITCODE -ne 0){throw 'Container startup failed'}
  Write-Output 'Initial password: .local/docker-secrets/admin-password'
} finally {Pop-Location}

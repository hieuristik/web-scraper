param(
  [string]$Image = "hieuristik/aa-scraper:latest",
  [string]$Username = $env:DOCKERHUB_USERNAME,
  [string]$Token = $env:DOCKERHUB_TOKEN
)

if (-not $Username) {
  Write-Error "No username provided. Either pass -Username or set environment variable DOCKERHUB_USERNAME."
  exit 1
}
if (-not $Token) {
  Write-Error "No token provided. Either pass -Token or set environment variable DOCKERHUB_TOKEN."
  exit 1
}

Write-Host "Logging in to Docker Hub as $Username..."
# Use --password-stdin to avoid showing the token in the shell's history
$Token | docker login --username $Username --password-stdin
if ($LASTEXITCODE -ne 0) {
  Write-Error "Docker login failed. Check username/token and try again."
  exit 1
}

# create buildx builder if missing
$builderExists = docker buildx inspect multi-builder 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Creating buildx builder 'multi-builder'..."
  docker buildx create --name multi-builder --use | Out-Null
} else {
  Write-Host "Using existing buildx builder 'multi-builder'..."
  docker buildx use multi-builder
}

Write-Host "Bootstrapping buildx..."
docker buildx inspect --bootstrap

Write-Host "Building and pushing multi-arch image: $Image"
# Build & push for amd64 and arm64
docker buildx build --platform linux/amd64,linux/arm64 -t $Image --push .
if ($LASTEXITCODE -ne 0) {
  Write-Error "docker buildx build failed."
  exit 1
}

Write-Host "Image pushed: $Image"
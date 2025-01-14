# Get the directory of the current script
$workdir = Split-Path -Parent -Path $MyInvocation.MyCommand.Path
Write-Host "workdir = '$workdir'"
Set-Location -Path $workdir

# Check if the ffmpeg.zip file already exists and exit if it does
if (Test-Path -Path "$workdir\ffmpeg.zip" -PathType Leaf) {
    Write-Host "ffmpeg.zip already exists. Exiting..."
    exit
}

# Download the latest FFmpeg release
Invoke-WebRequest -Uri "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" -OutFile "ffmpeg-release-amd64-static.tar.xz"
Invoke-WebRequest -Uri "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz.md5" -OutFile "ffmpeg-release-amd64-static.tar.xz.md5"

# Check package has not been altered
$md5File = Get-Content -Path "ffmpeg-release-amd64-static.tar.xz.md5"
$md5Hash = $md5File.Split(" ")[0]
$computedHash = Get-FileHash -Path "ffmpeg-release-amd64-static.tar.xz" -Algorithm MD5 | Select-Object -ExpandProperty Hash
Write-Output "MD5 hash=$md5Hash"
Write-Output "Computed MD5 hash=$computedHash"

# Abort if package was altered
if ($md5Hash -ne $computedHash) {
    Write-Host "MD5 digests don't match, check FFmpeg release. `nAborting..."
    exit 1
}

# Create build directory and extract the tar file
New-Item -ItemType Directory -Force -Path ".\build" | Out-Null
7z x "ffmpeg-release-amd64-static.tar.xz" 
tar -xf "ffmpeg-release-amd64-static.tar" -C ".\build"

# Create ffmpeg directory and copy the ffmpeg binary
New-Item -ItemType Directory -Force -Path ".\ffmpeg\bin" | Out-Null
$buildpath = Resolve-Path ".\build\ffmpeg-*-amd64-static\ffmpeg" | Select -ExpandProperty Path
Copy-Item -Path $buildpath -Destination ".\ffmpeg\bin\" -Force

# Change to ffmpeg directory and create a zip file
Set-Location -Path ".\ffmpeg"
Compress-Archive -Path ".\bin" -DestinationPath "..\ffmpeg.zip" -Force
#!/bin/sh
workdir=$(dirname -- "$( readlink -f -- "$0"; )";)
echo "workdir = '$workdir'"
cd "$workdir"

# Check if the ffmpeg.zip file already exists and exit if it does
if [ -f "$workdir/ffmpeg.zip" ]; then
    echo "ffmpeg.zip already exists. Exiting..."
    exit 0
fi

# Download the latest FFmpeg release
wget -O ffmpeg-release-amd64-static.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz 
wget -O ffmpeg-release-amd64-static.tar.xz.md5 https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz.md5
# check package has not been altered
diff=$(md5sum -c --quiet ffmpeg-release-amd64-static.tar.xz.md5)
# Abort if package was altered
if [ ! -z "$diff" ]; then
    echo "MD5 digests don't match, check FFmpeg release. \nAborting..."
    exit 1
fi

mkdir -p .build
tar xf ffmpeg-release-amd64-static.tar.xz --directory .build

mkdir -p ffmpeg/bin
cp .build/ffmpeg-*-amd64-static/ffmpeg ffmpeg/bin/
cd ffmpeg
zip -r ../ffmpeg.zip .
#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:/opt/conda/bin:/opt/flutter/bin:/usr/local/bin:$PATH"

failures=0

check_cmd() {
  local label="$1"
  local cmd="$2"

  echo
  echo "===== ${label} ====="
  if bash -lc "${cmd}"; then
    echo "[OK] ${label}"
  else
    echo "[FAIL] ${label}"
    failures=$((failures + 1))
  fi
}

echo "===== Install base packages ====="
$SUDO apt-get update
$SUDO apt-get install -y \
  curl \
  wget \
  git \
  ca-certificates \
  build-essential \
  software-properties-common \
  unzip \
  zip \
  xz-utils \
  gnupg \
  gpg \
  lsb-release \
  apt-transport-https \
  python3 \
  python3-pip \
  python3-venv \
  pipx \
  ruby-full \
  ruby-dev \
  php-cli \
  composer \
  openjdk-17-jdk \
  gradle \
  golang \
  rustc \
  cargo \
  nodejs \
  npm \
  clang \
  cmake \
  ninja-build \
  pkg-config \
  libgtk-3-dev \
  libstdc++-12-dev \
  libglu1-mesa

echo
echo "===== Install Python tools ====="
python3 -m pipx ensurepath || true
pipx install pipenv --force
pipx install pip-tools --force

echo
echo "===== Install Miniconda ====="
if [ ! -d /opt/conda ]; then
  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
  $SUDO bash /tmp/miniconda.sh -b -p /opt/conda
  rm -f /tmp/miniconda.sh
else
  echo "[SKIP] Miniconda already installed: /opt/conda"
fi

echo 'export PATH="/opt/conda/bin:$PATH"' | $SUDO tee /etc/profile.d/conda-path.sh >/dev/null
$SUDO chmod +x /etc/profile.d/conda-path.sh
export PATH="/opt/conda/bin:$PATH"

echo
echo "===== Install .NET SDK 8.0 ====="
if ! dpkg -s packages-microsoft-prod >/dev/null 2>&1; then
  wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O /tmp/packages-microsoft-prod.deb
  $SUDO dpkg -i /tmp/packages-microsoft-prod.deb
  rm -f /tmp/packages-microsoft-prod.deb
fi

$SUDO apt-get update
$SUDO apt-get install -y dotnet-sdk-8.0

echo
echo "===== Install Dart SDK ====="
$SUDO rm -f /usr/share/keyrings/dart.gpg
wget -qO- https://dl-ssl.google.com/linux/linux_signing_key.pub \
  | $SUDO gpg --dearmor -o /usr/share/keyrings/dart.gpg

echo 'deb [signed-by=/usr/share/keyrings/dart.gpg arch=amd64] https://storage.googleapis.com/download.dartlang.org/linux/debian stable main' \
  | $SUDO tee /etc/apt/sources.list.d/dart_stable.list >/dev/null

$SUDO apt-get update
$SUDO apt-get install -y dart

echo
echo "===== Install Flutter ====="
if [ ! -d /opt/flutter ]; then
  $SUDO git clone https://github.com/flutter/flutter.git /opt/flutter
else
  echo "[SKIP] Flutter already installed: /opt/flutter"
  $SUDO git -C /opt/flutter pull --ff-only || true
fi

$SUDO git config --global --add safe.directory /opt/flutter || true
echo 'export PATH="/opt/flutter/bin:$PATH"' | $SUDO tee /etc/profile.d/flutter-path.sh >/dev/null
$SUDO chmod +x /etc/profile.d/flutter-path.sh
export PATH="/opt/flutter/bin:$PATH"

echo
echo "===== Install CocoaPods ====="
$SUDO gem install cocoapods

echo
echo "===== Install Trivy ====="
$SUDO rm -f /usr/share/keyrings/trivy.gpg
wget -qO - https://get.trivy.dev/deb/public.key \
  | gpg --dearmor \
  | $SUDO tee /usr/share/keyrings/trivy.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://get.trivy.dev/deb generic main" \
  | $SUDO tee /etc/apt/sources.list.d/trivy.list >/dev/null

$SUDO apt-get update
$SUDO apt-get install -y trivy

echo
echo "===== Install Syft ====="
curl -sSfL https://get.anchore.io/syft | $SUDO sh -s -- -b /usr/local/bin

echo
echo "===== Optional project Python venv ====="
if [ -f requirements.txt ] || [ -f requierements.txt ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip

  if [ -f requirements.txt ]; then
    .venv/bin/pip install -r requirements.txt
  fi

  if [ -f requierements.txt ]; then
    .venv/bin/pip install -r requierements.txt
  fi
else
  echo "[SKIP] requirements.txt / requierements.txt not found"
fi

echo
echo "======================================"
echo " Verification"
echo "======================================"

echo
echo "===== OS ====="
cat /etc/os-release || true
uname -a || true

check_cmd "curl" "curl --version | head -n 1"
check_cmd "wget" "wget --version | head -n 1"
check_cmd "git" "git --version"
check_cmd "gcc/build-essential" "gcc --version | head -n 1 && make --version | head -n 1"

check_cmd "Python" "python3 --version"
check_cmd "pip" "python3 -m pip --version"
check_cmd "venv" "python3 -m venv /tmp/venv-check && /tmp/venv-check/bin/python --version"
check_cmd "pipx" "pipx --version"
check_cmd "pipenv" "pipenv --version"
check_cmd "pip-tools" "pip-compile --version"

check_cmd "Ruby" "ruby --version"
check_cmd "RubyGems" "gem --version"
check_cmd "Bundler" "bundle --version || gem install bundler && bundle --version"
check_cmd "CocoaPods" "pod --version"

check_cmd "PHP" "php --version | head -n 1"
check_cmd "Composer" "composer --version"

check_cmd "Java" "java -version"
check_cmd "Javac" "javac -version"
check_cmd "Gradle" "gradle --version | head -n 5"

check_cmd "Go" "go version"

check_cmd "Rust" "rustc --version"
check_cmd "Cargo" "cargo --version"

check_cmd "Node.js" "node --version"
check_cmd "npm" "npm --version"

check_cmd "Conda" "conda --version"

check_cmd ".NET SDK" "dotnet --version && dotnet --list-sdks"

check_cmd "Dart" "dart --version"

check_cmd "Flutter CLI" "flutter --version"

check_cmd "Trivy" "trivy --version"

check_cmd "Syft" "syft version"

check_cmd "Syft CycloneDX JSON" "syft dir:. -o cyclonedx-json >/tmp/syft-sbom.json && test -s /tmp/syft-sbom.json"

echo
echo "===== Summary ====="
if [ "${failures}" -eq 0 ]; then
  echo "[OK] All checks passed."
  echo
  echo "Перезайди в SSH или выполни:"
  echo "source /etc/profile.d/conda-path.sh"
  echo "source /etc/profile.d/flutter-path.sh"
else
  echo "[FAIL] ${failures} check(s) failed."
  exit 1
fi
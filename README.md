cat > reinstall_trivy.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo "===== Remove old Trivy package/repo/key ====="
$SUDO apt-get remove -y trivy || true
$SUDO apt-get purge -y trivy || true
$SUDO rm -f /etc/apt/sources.list.d/trivy.list
$SUDO rm -f /usr/share/keyrings/trivy.gpg
$SUDO rm -f /etc/apt/trusted.gpg.d/trivy.gpg
$SUDO apt-get autoremove -y || true

echo
echo "===== Install dependencies ====="
$SUDO apt-get update
$SUDO apt-get install -y wget gnupg ca-certificates apt-transport-https

echo
echo "===== Add fresh Trivy GPG key ====="
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key \
  | gpg --dearmor \
  | $SUDO tee /usr/share/keyrings/trivy.gpg >/dev/null

echo
echo "===== Add Trivy APT repository ====="
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" \
  | $SUDO tee /etc/apt/sources.list.d/trivy.list >/dev/null

echo
echo "===== Install Trivy ====="
$SUDO apt-get update
$SUDO apt-get install -y trivy

echo
echo "===== Verify Trivy ====="
command -v trivy
trivy --version

echo
echo "[OK] Trivy installed successfully"
EOF

chmod +x reinstall_trivy.sh
./reinstall_trivy.sh

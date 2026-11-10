#!/usr/bin/env bash
# EC2 user data, for a NEW Ubuntu Server 24.04 x86_64 instance only.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
    snap install amazon-ssm-agent --classic
fi
snap start amazon-ssm-agent
apt-get update
apt-get install -y ca-certificates curl unzip python3 postgresql-client
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
work_dir=$(mktemp -d)
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o "$work_dir/awscliv2.zip"
unzip -q "$work_dir/awscliv2.zip" -d "$work_dir"
"$work_dir/aws/install"
rm -rf "$work_dir"
install -d -m 700 /opt/moneycushion
install -d -m 755 /opt/moneycushion/models
install -d -m 755 /opt/moneycushion/certs
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /opt/moneycushion/certs/global-bundle.pem
docker --version
docker compose version
aws --version

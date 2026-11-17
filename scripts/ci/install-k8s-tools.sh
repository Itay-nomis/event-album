#!/usr/bin/env bash
# Download kustomize + kubeconform release binaries (checksum-verified) into $BIN_DIR
# (default /usr/local/bin). Usage: install-k8s-tools.sh <kustomize-version> <kubeconform-version>
set -euo pipefail
KUSTOMIZE_VERSION="$1"
KUBECONFORM_VERSION="$2"
tmp="$(mktemp -d)"
cd "$tmp"

k="kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"
curl -fsSLO "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/${k}"
curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/checksums.txt" \
  | grep " ${k}\$" | sha256sum -c -

c="kubeconform-linux-amd64.tar.gz"
curl -fsSLO "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/${c}"
curl -fsSL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/CHECKSUMS" \
  | grep " ${c}\$" | sha256sum -c -

tar -xzf "$k" kustomize
tar -xzf "$c" kubeconform
BIN_DIR="${BIN_DIR:-/usr/local/bin}"
SUDO=""
[ -w "$BIN_DIR" ] || SUDO="sudo"
$SUDO install -m 0755 kustomize kubeconform "$BIN_DIR/"
"$BIN_DIR/kustomize" version
"$BIN_DIR/kubeconform" -v

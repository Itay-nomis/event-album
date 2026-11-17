#!/usr/bin/env bash
# Render every Kustomize overlay and validate the result against Kubernetes JSON schemas.
# CRDs (KEDA ScaledObject, Prometheus Operator, ArgoCD Application, k3s HelmChartConfig) are
# checked against the community CRDs catalog; kinds missing there are skipped, not failed.
# Needs: kustomize, kubeconform (CI installs both; see .github/workflows/ci.yml).
set -euo pipefail
cd "$(dirname "$0")/.."

K8S_VERSION="${K8S_VERSION:-1.36.0}"
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
validate() {
  kubeconform -strict -summary -ignore-missing-schemas \
    -kubernetes-version "$K8S_VERSION" \
    -schema-location default -schema-location "$CRDS" "$@"
}

for overlay in k8s/overlays/*/; do
  echo "== kustomize build $overlay"
  kustomize build "$overlay" | validate -
done

echo "== argocd/ and k8s/cluster/"
validate argocd/root-app.yaml argocd/apps/*.yaml k8s/cluster/*.yaml

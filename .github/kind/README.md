# Kind Cluster Configuration

Configuration files for [kind](https://kind.sigs.k8s.io/) (Kubernetes-in-Docker) clusters used by the integration test workflow.

## Table of Contents

- [Files](#files)
- [Why Calico](#why-calico)
- [Usage](#usage)

## Files

| File | Description |
|------|-------------|
| `kind-calico.yaml` | Kind cluster config that disables the default kindnet CNI so Calico can be installed. Single control-plane node with `192.168.0.0/16` pod subnet. |

## Why Calico

The default kind CNI (kindnet) does not enforce `NetworkPolicy` resources. GCO deploys default-deny network policies in `lambda/kubectl-applier-simple/manifests/03-network-policies.yaml`. To actually validate that these policies work, the integration test installs Calico on top of kind, which enforces `NetworkPolicy` the same way a production CNI would.

## Usage

Used by the `integration:kind:cluster-e2e` job in `.github/workflows/integration-tests.yml`:

```yaml
- name: Create kind cluster
  uses: helm/kind-action@v1
  with:
    config: .github/kind/kind-calico.yaml
```

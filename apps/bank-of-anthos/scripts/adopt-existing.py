#!/usr/bin/env python3
"""Adopt the manual app's 22 resources into Helm; never adopt PVCs or Secrets."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


RESOURCES = [
    "configmap/environment-config", "configmap/service-api-config",
    "configmap/demo-data-config", "configmap/accounts-db-config",
    "configmap/ledger-db-config", "serviceaccount/bank-of-anthos",
    *[f"service/{name}" for name in (
        "accounts-db", "ledger-db", "userservice", "contacts", "balancereader",
        "transactionhistory", "ledgerwriter", "frontend",
    )],
    "statefulset/accounts-db", "statefulset/ledger-db",
    *[f"deployment/{name}" for name in (
        "userservice", "contacts", "balancereader", "transactionhistory",
        "ledgerwriter", "frontend",
    )],
]


def run(*command):
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def check_owner(resource, release, namespace):
    meta = resource["metadata"]
    labels = meta.get("labels") or {}
    annotations = meta.get("annotations") or {}
    expected = (
        (labels, "app.kubernetes.io/managed-by", "Helm"),
        (annotations, "meta.helm.sh/release-name", release),
        (annotations, "meta.helm.sh/release-namespace", namespace),
    )
    for fields, key, value in expected:
        if key in fields and fields[key] != value:
            raise RuntimeError(f"{meta['name']}: {key}={fields[key]!r}; expected {value!r}")
    for key in (
        "argocd.argoproj.io/instance", "argocd.argoproj.io/tracking-id",
        "kustomize.toolkit.fluxcd.io/name", "helm.toolkit.fluxcd.io/name",
    ):
        if key in labels or key in annotations:
            raise RuntimeError(f"{meta['name']}: already tracked by another controller ({key})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="bankops-app")
    parser.add_argument("--release", default="bankops")
    parser.add_argument("--check", action="store_true", help="Read-only ownership check")
    args = parser.parse_args()
    kube = ("kubectl", "-n", args.namespace)

    # Never transfer resources away from an already recorded Helm release.
    releases = json.loads(run("helm", "list", "-n", args.namespace, "--all", "-o", "json"))
    if any(item["name"] == args.release for item in releases):
        raise RuntimeError(f"Release {args.release!r} already exists; use helm status/history/upgrade")

    objects = []
    for target in RESOURCES:
        resource = json.loads(run(*kube, "get", target, "-o", "json"))
        if resource["metadata"].get("namespace") != args.namespace:
            raise RuntimeError(f"Wrong namespace for {target}")
        check_owner(resource, args.release, args.namespace)
        objects.append(resource)

    # Record the claims actually mounted by the existing DB Pods, not hardcoded PV names.
    claims = set()
    for resource in objects:
        if resource["kind"] == "StatefulSet":
            for volume in resource["spec"]["template"]["spec"].get("volumes", []):
                if "persistentVolumeClaim" in volume:
                    claims.add(volume["persistentVolumeClaim"]["claimName"])
    if len(claims) != 2:
        raise RuntimeError("Expected two distinct existing database PVCs; review StatefulSet storage")
    pvcs = [json.loads(run(*kube, "get", "pvc", name, "-o", "json")) for name in sorted(claims)]
    for pvc in pvcs:
        if pvc.get("status", {}).get("phase") != "Bound":
            raise RuntimeError(f"PVC {pvc['metadata']['name']} is not Bound")

    print(f"Checked {len(objects)} resources for release {args.release!r} in {args.namespace!r}.")
    for pvc in pvcs:
        print(f"Keep PVC {pvc['metadata']['name']} -> PV {pvc['spec']['volumeName']}")
    if args.check:
        print("Read-only check complete. No metadata changed.")
        return

    os.umask(0o077)
    parent = Path.home() / "bankops-helm-backups"
    parent.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="before-adoption-", dir=parent))
    snapshot = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "release": args.release, "namespace": args.namespace,
        "resources": objects, "pvcs": pvcs,
    }
    (backup / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"Configuration/PVC mapping snapshot: {backup}/snapshot.json")
    print("This snapshot is not a PostgreSQL data backup.")

    # All checks run before mutations. No --overwrite: concurrent owner changes must fail.
    run(*kube, "label", *RESOURCES, "app.kubernetes.io/managed-by=Helm")
    run(*kube, "annotate", *RESOURCES,
        f"meta.helm.sh/release-name={args.release}",
        f"meta.helm.sh/release-namespace={args.namespace}")
    print("Ownership metadata updated for 22 resources. PVCs and Secrets were not adopted.")
    print("Next: run the Helm server dry-run and upgrade commands in chart/README.md.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

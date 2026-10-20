"""Render and migration checks. Run on the laptop with Helm and PyYAML installed."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


APP = Path(__file__).resolve().parents[1]
CHART = APP / "chart"


def helm_render(values=None, value_files=(), expect_success=True):
    with tempfile.TemporaryDirectory() as directory:
        command = ["helm", "template", "bankops", str(CHART), "-n", "bankops-app"]
        for path in value_files:
            command.extend(["-f", str(path)])
        if values is not None:
            path = Path(directory) / "values.json"
            path.write_text(json.dumps(values))
            command.extend(["-f", str(path)])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    if expect_success and result.returncode:
        raise AssertionError(result.stderr)
    if not expect_success:
        if result.returncode == 0:
            raise AssertionError("Invalid values unexpectedly rendered successfully")
        return result.stderr
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def indexed(objects):
    return {(item["kind"], item["metadata"]["name"]): item for item in objects}


class ChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = helm_render()
        cls.resources = indexed(cls.objects)
        cls.originals = []
        for path in (APP / "kubernetes-manifests").glob("*.yaml"):
            cls.originals.extend(item for item in yaml.safe_load_all(path.read_text()) if item)

    def test_default_inventory_and_external_storage(self):
        self.assertEqual(len(self.objects), 22)
        wanted = {(item["kind"], item["metadata"]["name"]) for item in self.originals
                  if item["kind"] not in ("Namespace", "PersistentVolumeClaim")
                  and item["metadata"]["name"] != "loadgenerator"}
        self.assertEqual(set(self.resources), wanted)
        self.assertTrue(all(item["kind"] not in ("Namespace", "Secret", "PersistentVolumeClaim")
                            for item in self.objects))

    def test_selectors_images_probes_and_database_identity(self):
        for original in self.originals:
            kind, name = original["kind"], original["metadata"]["name"]
            if kind not in ("Deployment", "StatefulSet") or name == "loadgenerator":
                continue
            rendered = self.resources[kind, name]
            self.assertEqual(rendered["spec"]["selector"], original["spec"]["selector"])
            self.assertEqual(rendered["spec"]["template"]["metadata"]["labels"],
                             original["spec"]["template"]["metadata"]["labels"])
            old = original["spec"]["template"]["spec"]["containers"][0]
            new = rendered["spec"]["template"]["spec"]["containers"][0]
            for field in ("image", "resources", "volumeMounts", "startupProbe", "readinessProbe", "livenessProbe"):
                self.assertEqual(new.get(field), old.get(field), (name, field))
            if kind == "StatefulSet":
                self.assertEqual(rendered["spec"]["replicas"], 1)
                self.assertEqual(rendered["spec"]["serviceName"], original["spec"]["serviceName"])
                self.assertEqual(rendered["spec"]["template"]["spec"]["volumes"],
                                 original["spec"]["template"]["spec"]["volumes"])

    def test_services_resolve_to_matching_pods(self):
        pods = [item["spec"]["template"] for item in self.objects
                if item["kind"] in ("Deployment", "StatefulSet")]
        for item in self.objects:
            if item["kind"] == "Service":
                selector = item["spec"]["selector"]
                matches = [pod for pod in pods if all(pod["metadata"]["labels"].get(key) == value
                                                      for key, value in selector.items())]
                self.assertEqual(len(matches), 1, item["metadata"]["name"])

    def test_configmap_and_secret_references_are_typed_correctly(self):
        configmaps = {name for kind, name in self.resources if kind == "ConfigMap"}
        expected_secrets = {"accounts-db-secret", "ledger-db-secret", "jwt-key"}
        for item in self.objects:
            if item["kind"] not in ("Deployment", "StatefulSet"):
                continue
            spec = item["spec"]["template"]["spec"]
            for container in spec["containers"]:
                for reference in container.get("envFrom", []):
                    if "configMapRef" in reference:
                        self.assertIn(reference["configMapRef"]["name"], configmaps)
                    if "secretRef" in reference:
                        self.assertIn(reference["secretRef"]["name"], expected_secrets)
            for volume in spec.get("volumes", []):
                if "secret" in volume:
                    self.assertIn(volume["secret"]["secretName"], expected_secrets)

    def test_values_override_external_references_and_workload_configuration(self):
        result = indexed(helm_render({
            "accountsDb": {"existingClaim": "accounts-volume", "existingSecret": "accounts-credentials"},
            "ledgerDb": {"existingClaim": "ledger-volume", "existingSecret": "ledger-credentials"},
            "jwt": {"existingSecret": "signing-keys"},
            "frontend": {"replicas": 2, "image": "example.com/frontend:test"},
            "nodeSelector": {"bankops.io/workload": "true"},
        }))
        front = result["Deployment", "frontend"]
        self.assertEqual(front["spec"]["replicas"], 2)
        self.assertEqual(front["spec"]["template"]["spec"]["containers"][0]["image"], "example.com/frontend:test")
        for (kind, name), item in result.items():
            if kind not in ("Deployment", "StatefulSet"):
                continue
            spec = item["spec"]["template"]["spec"]
            self.assertEqual(spec["nodeSelector"], {"bankops.io/workload": "true"})
            for reference in spec["containers"][0].get("envFrom", []):
                if "secretRef" in reference:
                    self.assertIn(reference["secretRef"]["name"], {"accounts-credentials", "ledger-credentials"})
            for volume in spec.get("volumes", []):
                if "secret" in volume:
                    self.assertEqual(volume["secret"]["secretName"], "signing-keys")
                if "persistentVolumeClaim" in volume:
                    self.assertEqual(volume["persistentVolumeClaim"]["claimName"],
                                     "accounts-volume" if name == "accounts-db" else "ledger-volume")

    def test_nodeport_ingress_tls_and_loadgenerator(self):
        objects = helm_render({
            "loadgenerator": {"enabled": True},
            "frontend": {"service": {"port": 8081}},
            "ingress": {"enabled": True, "host": "bankops.example.com",
                        "tls": {"enabled": True, "secretName": "bankops-tls"}},
        }, [APP / "environments/nodeport.yaml"])
        resources = indexed(objects)
        self.assertEqual(len(objects), 24)
        service = resources["Service", "frontend"]["spec"]
        self.assertEqual(service["type"], "NodePort")
        self.assertEqual(service["ports"][0]["nodePort"], 30080)
        ingress = resources["Ingress", "frontend"]["spec"]
        self.assertEqual(ingress["tls"][0]["secretName"], "bankops-tls")
        self.assertEqual(ingress["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["number"], 8081)
        env = resources["Deployment", "loadgenerator"]["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertIn({"name": "FRONTEND_ADDR", "value": "frontend:8081"}, env)

    def test_config_checksum_and_jvm_options(self):
        changed = indexed(helm_render({"app": {"localRoutingNum": "123456789"}}))
        for (kind, name), item in self.resources.items():
            if kind not in ("Deployment", "StatefulSet"):
                continue
            old = item["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
            new = changed[kind, name]["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
            self.assertNotEqual(old, new)
        for name in ("balancereader", "transactionhistory", "ledgerwriter"):
            env = self.resources["Deployment", name]["spec"]["template"]["spec"]["containers"][0]["env"]
            self.assertTrue(any(item["name"] == "JAVA_TOOL_OPTIONS" for item in env))
            self.assertFalse(any(item["name"] == "JVM_OPTS" for item in env))

    def test_schema_rejects_invalid_external_refs_types_and_database_scaling(self):
        for bad in (
            {"accountsDb": {"existingClaim": ""}},
            {"jwt": {"existingSecret": ""}},
            {"frontend": {"replicas": -1}},
            {"accountsDb": {"replicas": 2}},
            {"loadgenerator": {"enabled": "false"}},
            {"app": {"enableTracing": False}},
            {"frontend": {"service": {"nodePort": 8080}}},
            {"ingress": {"enabled": True, "tls": {"enabled": True, "secretName": ""}}},
        ):
            with self.subTest(values=bad):
                helm_render(bad, expect_success=False)


spec = importlib.util.spec_from_file_location("bankops_adoption", APP / "scripts/adopt-existing.py")
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.objects = {}
        for item in helm_render():
            item = copy.deepcopy(item)
            item["metadata"]["namespace"] = "bankops-app"
            item["metadata"]["labels"].pop("app.kubernetes.io/managed-by", None)
            kinds = {"ConfigMap": "configmap", "ServiceAccount": "serviceaccount", "Service": "service",
                     "Deployment": "deployment", "StatefulSet": "statefulset"}
            self.objects[f"{kinds[item['kind']]}/{item['metadata']['name']}"] = item
        self.mutations = []

    def fake_run(self, *command):
        if command[0] == "helm":
            return "[]"
        if command[3] == "get":
            if command[4] == "pvc":
                return json.dumps({"metadata": {"name": command[5], "uid": "existing-uid"},
                                   "spec": {"volumeName": "existing-pv"}, "status": {"phase": "Bound"}})
            return json.dumps(self.objects[command[4]])
        self.mutations.append(command)
        return ""

    def test_check_mode_is_read_only(self):
        with patch.object(adoption, "run", side_effect=self.fake_run), \
             patch.object(sys, "argv", ["adopt-existing.py", "--check"]), redirect_stdout(io.StringIO()):
            adoption.main()
        self.assertEqual(self.mutations, [])

    def test_foreign_owner_stops_before_any_mutation(self):
        self.objects["deployment/frontend"]["metadata"].setdefault("annotations", {})[
            "meta.helm.sh/release-name"] = "someone-else"
        with patch.object(adoption, "run", side_effect=self.fake_run), \
             patch.object(sys, "argv", ["adopt-existing.py"]), self.assertRaises(RuntimeError):
            adoption.main()
        self.assertEqual(self.mutations, [])

    def test_snapshot_and_exact_adoption_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(adoption, "run", side_effect=self.fake_run), \
                 patch.object(adoption.Path, "home", return_value=Path(directory)), \
                 patch.object(sys, "argv", ["adopt-existing.py"]), redirect_stdout(io.StringIO()):
                adoption.main()
            snapshot = next(Path(directory).rglob("snapshot.json"))
            self.assertEqual(len(json.loads(snapshot.read_text())["resources"]), 22)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.mutations), 2)
        for command in self.mutations:
            self.assertNotIn("--overwrite", command)
            targets = {part for part in command if part.startswith(tuple(
                f"{kind}/" for kind in ("configmap", "serviceaccount", "service", "statefulset", "deployment")))}
            self.assertEqual(targets, set(adoption.RESOURCES))


if __name__ == "__main__":
    unittest.main()

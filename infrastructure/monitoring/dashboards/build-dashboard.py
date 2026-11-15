#!/usr/bin/env python3
"""Sinh dashboard BankOps và ConfigMap từ code, không gọi API Grafana."""
import json
from pathlib import Path
from textwrap import dedent

# UID datasource do kube-prometheus-stack provision mặc định.
# Nếu datasource của bạn có UID khác, đổi ở đây rồi chạy lại script.
DATASOURCE = {"type": "prometheus", "uid": "prometheus"}
OUTPUT_DIR = Path(__file__).resolve().parent
panels = []


def add_panel(title, queries, *, kind="timeseries", unit="short",
              instant=False, description=""):
    """Thêm panel; queries là danh sách các cặp (PromQL, legend)."""
    panel_id = len(panels) + 1
    targets = []
    for index, (expression, legend) in enumerate(queries):
        targets.append({
            "refId": chr(ord("A") + index),
            "datasource": DATASOURCE,
            "editorMode": "code",
            "expr": dedent(expression).strip(),
            "legendFormat": legend,
            "instant": instant,
            "range": not instant,
            "format": "table" if kind == "table" else "time_series",
            "interval": "30s",  # Khớp scrape interval của bài.
        })

    panel = {
        "id": panel_id,
        "title": title,
        "type": kind,
        "description": description,
        "datasource": DATASOURCE,
        # Grafana có lưới 24 cột: mỗi panel rộng 12, hai panel mỗi hàng.
        "gridPos": {
            "x": ((panel_id - 1) % 2) * 12,
            "y": ((panel_id - 1) // 2) * 8,
            "w": 12,
            "h": 8,
        },
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "noValue": "NO DATA"},
            "overrides": [],
        },
        "options": {},
    }
    if kind == "timeseries":
        panel["options"] = {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
    elif kind == "stat":
        panel["options"] = {
            "reduceOptions": {
                "calcs": ["lastNotNull"], "fields": "", "values": False
            },
            "colorMode": "value",
            "graphMode": "none",
            "textMode": "auto",
        }
    elif kind == "table":
        panel["options"] = {"showHeader": True}
    panels.append(panel)
    return panel  # Cho phép bổ sung mapping/threshold riêng của panel.


http_panel = add_panel(
    "BankOps HTTP probe",
    [('probe_success{job="bankops-http"}', "{{instance}}")],
    kind="stat", instant=True, unit="none",
    description="1: HTTP probe thành công; 0: thất bại. NO DATA cần điều tra."
)
http_panel["fieldConfig"]["defaults"]["mappings"] = [{
    "type": "value",
    "options": {
        "0": {"text": "DOWN", "color": "red", "index": 0},
        "1": {"text": "UP", "color": "green", "index": 1},
    },
}]

add_panel(
    "HTTP probe duration",
    [('probe_duration_seconds{job="bankops-http"}', "{{instance}}")],
    unit="s",
    description="Thời gian một lần HTTP probe; không phải p95 latency nghiệp vụ."
)

add_panel(
    "BankOps CPU by Pod (cores)",
    [('''
        sum by (pod) (
          rate(container_cpu_usage_seconds_total{
            namespace="${namespace}",container!="",container!="POD"
          }[$__rate_interval])
        )
    ''', "{{pod}}")],
    description="0.2 = 0,2 CPU core; không tự động là 20% tổng CPU node."
)

add_panel(
    "BankOps memory working set",
    [('''
        sum by (pod) (
          container_memory_working_set_bytes{
            namespace="${namespace}",container!="",container!="POD"
          }
        )
    ''', "{{pod}}")],
    unit="bytes",
    description="Bytes, Grafana hiển thị MiB/GiB; working set không chỉ là heap."
)

add_panel(
    "Container restarts in 15m",
    [('''
        sum by (pod, container) (
          increase(kube_pod_container_status_restarts_total{
            namespace="${namespace}"
          }[15m])
        )
    ''', "{{pod}} / {{container}}")],
    kind="table", instant=True, unit="none",
    description="Mức tăng counter trong 15 phút; có thể có số thập phân."
)

add_panel(
    "Containers currently in CrashLoopBackOff",
    [('''
        max by (pod, container) (
          kube_pod_container_status_waiting_reason{
            namespace="${namespace}",reason="CrashLoopBackOff"
          }
        ) == 1
    ''', "{{pod}} / {{container}}")],
    kind="table", instant=True, unit="none",
    description=("Bảng có thể trống khi không có CrashLoop. Nếu các panel khác "
                 "cũng trống, kiểm tra datasource/kube-state-metrics.")
)

add_panel(
    "Desired vs available replicas",
    [
        ('kube_deployment_spec_replicas{namespace="${namespace}"}',
         "{{deployment}} desired"),
        ('kube_deployment_status_replicas_available{namespace="${namespace}"}',
         "{{deployment}} available"),
    ],
    unit="none",
    description="Chỉ Deployment; database StatefulSet ở panel riêng."
)

add_panel(
    "Database ready replicas",
    [('''
        kube_statefulset_status_replicas_ready{
          namespace="${namespace}",statefulset=~"accounts-db|ledger-db"
        }
    ''', "{{statefulset}}")],
    kind="stat", instant=True, unit="none",
    description="Mỗi DB hiện cần 1 replica Ready; chưa chứng minh query DB thành công."
)

add_panel(
    "Node CPU busy",
    [('''
        100 * (1 - avg by (instance) (
          rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])
        ))
    ''', "{{instance}}")],
    unit="percent",
    description="CPU bận trên từng node, giá trị từ 0 tới 100."
)

add_panel(
    "Node memory used",
    [('''
        100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
    ''', "{{instance}}")],
    unit="percent",
    description="Tính theo MemAvailable, không chỉ MemFree."
)

filesystem_panel = add_panel(
    "Node filesystem available",
    [('''
        100 * (
          node_filesystem_avail_bytes{
            mountpoint=~"/|/var/lib/longhorn",fstype!~"tmpfs|overlay|squashfs"
          }
          /
          node_filesystem_size_bytes{
            mountpoint=~"/|/var/lib/longhorn",fstype!~"tmpfs|overlay|squashfs"
          }
        )
    ''', "{{instance}} {{mountpoint}}")],
    unit="percent",
    description=("Phần trăm còn trống, càng thấp càng xấu. Đo filesystem node, "
                 "không thay thế dung lượng PVC/sức khỏe replica Longhorn.")
)
filesystem_panel["fieldConfig"]["defaults"]["thresholds"] = {
    "mode": "absolute",
    "steps": [
        {"color": "red", "value": None},
        {"color": "orange", "value": 15},
        {"color": "green", "value": 25},
    ],
}
filesystem_panel["fieldConfig"]["defaults"]["custom"] = {
    "thresholdsStyle": {"mode": "line"}
}

add_panel(
    "BankOps custom alerts",
    [('ALERTS{scope="bankops",alertstate=~"pending|firing"}', "{{alertname}}")],
    kind="table", instant=True, unit="none",
    description=("Bảng có thể trống khi không có active alert. "
                 "Firing trong bảng chưa chứng minh Slack đã nhận.")
)

# Các panel có unit percent ở đây đều sử dụng số liệu 0..100.
for panel in panels:
    if panel["fieldConfig"]["defaults"]["unit"] == "percent":
        panel["fieldConfig"]["defaults"].update({"min": 0, "max": 100})

dashboard = {
    "id": None,
    "uid": "bankops-overview",  # Giữ ổn định khi update để giữ URL dashboard.
    "title": "BankOps Overview",
    "tags": ["bankops", "kubernetes", "as-code"],
    "timezone": "Asia/Ho_Chi_Minh",
    "schemaVersion": 39,  # Classic JSON; Grafana hỗ trợ migration khi nạp.
    "version": 1,
    "editable": False,  # Cập nhật qua code thay vì Save trực tiếp trong UI.
    "refresh": "30s",
    "time": {"from": "now-30m", "to": "now"},
    "templating": {
        "list": [{
            "name": "namespace", "type": "constant", "hide": 2,
            "query": "bankops-app",
            "current": {"text": "bankops-app", "value": "bankops-app"},
            "options": [{"text": "bankops-app", "value": "bankops-app", "selected": True}],
        }]
    },
    "panels": panels,
}

# Một nguồn code sinh cả JSON lẫn ConfigMap; không cần PyYAML hay kubectl trên laptop.
dashboard_json = json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n"
(OUTPUT_DIR / "bankops-overview.json").write_text(dashboard_json, encoding="utf-8")

configmap_yaml = '''apiVersion: v1
kind: ConfigMap
metadata:
  name: bankops-overview-dashboard
  namespace: monitoring
  labels:
    grafana_dashboard: "1"
data:
  bankops-overview.json: |
'''
configmap_yaml += "".join("    " + line + "\n" for line in dashboard_json.splitlines())
(OUTPUT_DIR / "bankops-overview-configmap.yaml").write_text(configmap_yaml, encoding="utf-8")
print(f"Generated {len(panels)} panels in {OUTPUT_DIR}")
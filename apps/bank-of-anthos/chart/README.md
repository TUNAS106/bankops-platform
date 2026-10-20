# Bank of Anthos Helm chart cho BankOps

Chart triển khai toàn bộ app: hai PostgreSQL StatefulSet, năm backend và frontend. Mặc định có **22 tài nguyên**, giữ nguyên tên và selector của bản manifest thủ công để có thể chuyển app đang chạy sang release `bankops` trong namespace `bankops-app`.

Loadgenerator và Ingress là tùy chọn, mặc định tắt. Image được ghim phiên bản `v0.6.11` cùng digest. Hai database luôn có một Pod mỗi database.

## 1. Điều kiện trước khi chạy

- Helm 4; chart và lệnh trong bài được kiểm chứng với Helm **v4.2.0**.
- `kubectl` và Helm cùng truy cập đúng cluster RKE2.
- Namespace `bankops-app` tồn tại.
- Hai PVC bên dưới đã `Bound`, dùng Longhorn.
- Ba Secret bên dưới đã tồn tại trong cùng namespace.

| Tài nguyên ngoài chart | Tên mặc định | Nội dung cần có |
|---|---|---|
| PVC accounts | `accounts-db-data` | Data directory PostgreSQL accounts |
| PVC ledger | `ledger-db-data` | Data directory PostgreSQL ledger |
| Secret accounts | `accounts-db-secret` | `POSTGRES_PASSWORD`, `ACCOUNTS_DB_URI` |
| Secret ledger | `ledger-db-secret` | `POSTGRES_PASSWORD`, `SPRING_DATASOURCE_PASSWORD` |
| JWT Secret | `jwt-key` | `jwtRS256.key`, `jwtRS256.key.pub` |

Tên PVC/Secret cấu hình được trong `values.yaml`; chart tham chiếu và không tạo/xóa những tài nguyên này. Password database và private key không được ghi vào Git.

Accounts URI phải trỏ tới `accounts-db:5432/accounts-db`, user `accounts-admin`. Ledger dùng database `postgresdb`, user `admin`, Service `ledger-db:5432`. Hai giá trị password trong mỗi Secret phải khớp nhau và khớp password bên trong database đã khởi tạo.

Nếu cài app lần đầu trên cluster trống, tạo namespace và PVC bằng các file trong [kubernetes-manifests](../kubernetes-manifests), rồi tạo ba Secret theo hướng dẫn triển khai thủ công trước. Chart này ưu tiên dùng lại storage/credential đã có để phục vụ migration hiện tại.

## 2. Cấu trúc

```text
bank-of-anthos/
├── chart/
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── values.schema.json
│   └── templates/
├── environments/
│   ├── demo.yaml
│   ├── nodeport.yaml
│   └── java-compat.yaml
├── scripts/adopt-existing.py
├── tests/test_helm_chart.py
└── kubernetes-manifests/
```

`values.schema.json` kiểm tra kiểu, tên tham chiếu, port và các key cấu hình. File override chỉ cần chứa những giá trị khác mặc định. Một chart chỉ cài **một release trong mỗi namespace**, vì các tên Service/workload được giữ cố định để tương thích app hiện tại.

## 3. Kiểm tra và commit trên laptop

Chạy tại **repo `bankops-platform`**:

```bash
cd /home/tunas/project/devops/bankops-platform-project/bankops-platform

helm lint ./apps/bank-of-anthos/chart \
  -f ./apps/bank-of-anthos/environments/demo.yaml

helm template bankops ./apps/bank-of-anthos/chart \
  -n bankops-app \
  -f ./apps/bank-of-anthos/environments/demo.yaml \
  > /tmp/bankops-helm-rendered.yaml
```

Kiểm tra thay đổi rồi đẩy lên GitHub:

```bash
git add apps/bank-of-anthos/chart/ \
  apps/bank-of-anthos/environments/ \
  apps/bank-of-anthos/scripts/ \
  apps/bank-of-anthos/tests/ \
  apps/bank-of-anthos/.gitignore

git diff --cached --stat
git commit -m "feat: complete Bank of Anthos Helm chart"
git push origin main
```

## 4. Pull và kiểm tra trên EC2 master

```bash
cd ~/bankops-platform
git pull --ff-only origin main

helm version --short
kubectl get nodes
helm list -n bankops-app --all

kubectl -n bankops-app get pods,pvc
kubectl -n bankops-app get secret accounts-db-secret ledger-db-secret jwt-key
```

Nếu Helm không kết nối được nhưng kubectl vẫn chạy, kiểm tra kubeconfig. Alias dành riêng cho kubectl không tự cung cấp credential cho Helm. Khi user có quyền đọc kubeconfig phù hợp, đặt `KUBECONFIG` hoặc dùng `--kubeconfig`. Xem [RKE2 Cluster Access](https://docs.rke2.io/cluster_access).

Render và so sánh với tài nguyên thật trước khi tiếp nhận:

```bash
helm template bankops ./apps/bank-of-anthos/chart \
  -n bankops-app \
  -f ./apps/bank-of-anthos/environments/demo.yaml \
  > /tmp/bankops-helm-rendered.yaml

kubectl -n bankops-app apply --dry-run=server \
  -f /tmp/bankops-helm-rendered.yaml

kubectl -n bankops-app diff -f /tmp/bankops-helm-rendered.yaml
```

`kubectl diff` có thể trả exit code 1 khi có khác biệt. Đọc diff trước khi chuyển. Các thay đổi có chủ đích của chart hoàn chỉnh gồm:

- Thêm metadata quản lý Helm; không đổi selector/label Pod dùng để định tuyến.
- Thêm checksum ConfigMap vào Pod template để thay ConfigMap sẽ tạo rollout.
- Dùng `JAVA_TOOL_OPTIONS` có hiệu lực thay `JVM_OPTS` mà entrypoint Jib không sử dụng. Heap Java tối đa 256 MiB, memory limit container giữ 512 MiB.
- Cấu hình frontend hiển thị AWS/BankOps, tắt truy vấn metadata GCP bằng địa chỉ localhost không có dịch vụ.
- ServiceAccount không tự mount Kubernetes API token cho app.
- File demo hạ giới hạn cache balancereader xuống 1000 cho lab nhỏ.

Lần chuyển này có thể restart các Pod, kể cả database một replica. UID PVC và PV cần giữ nguyên; kiểm tra dữ liệu nghiệp vụ riêng trước và sau, không chỉ tài khoản demo. Tạo backup dữ liệu phù hợp trước một đợt thay đổi nếu dữ liệu cần giữ.

## 5. Tiếp nhận tài nguyên đã tạo bằng kubectl: làm một lần

Chỉ làm phần này khi app đang được quản lý thủ công. Nếu release `bankops` đã tồn tại, dùng `helm status/history/upgrade` ở phần 6, không chạy lại script tiếp nhận.

Trên master, script cần Python 3 chuẩn, `kubectl` và Helm; không cần PyYAML:

```bash
python3 apps/bank-of-anthos/scripts/adopt-existing.py --check
```

Lệnh `--check` chỉ đọc. Nó kiểm tra đủ 22 tài nguyên, kiểm tra ownership hiện có và hai PVC thật mà StatefulSet đang mount. Nếu tài nguyên thuộc release/controller khác, script dừng trước khi sửa metadata.

Sau khi đã xem diff, gắn ownership:

```bash
python3 apps/bank-of-anthos/scripts/adopt-existing.py
```

Script lưu cấu hình và mapping PVC vào `~/bankops-helm-backups/before-adoption-*/snapshot.json`, rồi gắn label/annotation cho đúng 22 tài nguyên. Snapshot này **không phải backup dữ liệu PostgreSQL**. PVC, Secret, Namespace và Ingress ngoài chart không được tiếp nhận. Các tài nguyên tùy chọn không được script chiếm từ một cài đặt khác.

Script không dùng `--overwrite`, không xóa Pod và không chạy Helm install. Nếu một thao tác metadata thất bại giữa chừng, đọc lỗi và xử lý owner; không có cơ chế transaction tự hoàn tác toàn bộ metadata.

Nếu namespace/release của bạn khác mặc định, truyền cùng tên vào cả script và Helm:

```bash
python3 apps/bank-of-anthos/scripts/adopt-existing.py \
  --namespace bankops-app --release bankops --check
```

## 6. Dry-run và triển khai bằng Helm

Sau khi tiếp nhận metadata, kiểm tra bằng Helm trên **EC2 master**:

```bash
helm upgrade --install bankops ./apps/bank-of-anthos/chart \
  -n bankops-app \
  -f ./apps/bank-of-anthos/environments/demo.yaml \
  --server-side=false \
  --dry-run=server \
  --hide-secret
```

Khi thành công, chạy bản thật:

```bash
helm upgrade --install bankops ./apps/bank-of-anthos/chart \
  -n bankops-app \
  -f ./apps/bank-of-anthos/environments/demo.yaml \
  --server-side=false \
  --wait \
  --timeout 10m
```

`--server-side=false` là cú pháp Helm 4 dùng trong bài tiếp nhận này. Không thêm `--force-replace`, `--force-conflicts` hoặc rollback/uninstall tự động để bỏ qua lỗi trong lần chuyển đầu. Xem [Helm upgrade](https://helm.sh/docs/helm/helm_upgrade/).

Kiểm tra:

```bash
helm status bankops -n bankops-app
helm history bankops -n bankops-app
kubectl -n bankops-app get pods,svc,pvc -o wide
kubectl -n bankops-app get pvc \
  -o custom-columns=NAME:.metadata.name,UID:.metadata.uid,PV:.spec.volumeName
```

Mặc định mong đợi 8 Pod ứng dụng sẵn sàng. Login, chuyển tiền và đọc lại dữ liệu cũ để kiểm chứng. Trạng thái release `deployed` không thay thế việc kiểm tra nghiệp vụ.

Nếu có timeout:

```bash
kubectl -n bankops-app get events --sort-by=.metadata.creationTimestamp
kubectl -n bankops-app get pods
helm status bankops -n bankops-app
```

Đọc `describe pod` và log của Pod lỗi, sửa values/template rồi upgrade lại. Không uninstall để thử lại: Helm sẽ xóa workload, Service và ConfigMap mà release đang quản lý.

## 7. Truy cập frontend qua public IP EC2

### Giữ cách port-forward hiện tại

Trên master:

```bash
kubectl -n bankops-app port-forward --address 0.0.0.0 svc/frontend 18080:80
```

Mở TCP 18080 từ public IP laptop trong SG của master và truy cập `http://PUBLIC_IP_MASTER:18080`. Giữ terminal mở; nếu Pod bị thay thì chạy lại lệnh.

### Dùng NodePort để không phải giữ port-forward

Thêm file override NodePort khi dry-run/upgrade:

```bash
helm upgrade --install bankops ./apps/bank-of-anthos/chart \
  -n bankops-app \
  -f ./apps/bank-of-anthos/environments/demo.yaml \
  -f ./apps/bank-of-anthos/environments/nodeport.yaml \
  --server-side=false \
  --wait --timeout 10m
```

Mở SG **Custom TCP 30080, Source My IP** trên EC2 bạn truy cập, rồi mở `http://PUBLIC_IP_EC2:30080`. Node cần có đường mạng vào cluster và Kubernetes Service networking hoạt động. NodePort không tự tạo AWS Load Balancer.

Các lần upgrade sau tiếp tục truyền cùng bộ file `-f`, để không vô tình quay về Service ClusterIP mặc định.

## 8. Các tùy chọn chính

| Nhóm values | Tác dụng |
|---|---|
| `accountsDb`, `ledgerDb` | Image/resources, tên PVC và Secret hiện có; database replicas cố định 1 |
| `userservice`, `contacts`, `balancereader`, `transactionhistory`, `ledgerwriter`, `frontend` | Image, replicas, resources riêng từng service |
| `app` | Phiên bản hiển thị, routing number, log level, cờ telemetry |
| `java.toolOptions` | Tùy chọn JVM được entrypoint Java đọc qua `JAVA_TOOL_OPTIONS` |
| `jwt.existingSecret` | Tên Secret JWT; tên key bên trong vẫn cố định theo app |
| `nodeSelector`, `tolerations`, `imagePullSecrets` | Cấu hình Pod áp dụng cho các workload |
| `frontend.service` | ClusterIP hoặc NodePort, Service port và nodePort |
| `loadgenerator.enabled` | Bật Deployment tạo tải, mặc định false |
| `ingress` | Tạo Ingress HTTP/TLS trỏ tới frontend; cần controller hoạt động |

Đổi ConfigMap trong chart sẽ đổi checksum và có thể rollout workload. Thay dữ liệu trong Secret tạo bên ngoài chart không tự đổi checksum: sau khi rotate credential/JWT key, chủ động đồng bộ và restart các workload liên quan.

### Java compatibility

Nếu log thực tế của image cũ báo lỗi `CgroupV2Subsystem` / `CgroupInfo.getMountPoint()`, thêm `-f ./apps/bank-of-anthos/environments/java-compat.yaml` sau demo.yaml. Override này tắt container support trong JVM nhưng vẫn đặt heap và processor count rõ ràng; quota Kubernetes vẫn áp dụng.

### Ingress tùy chọn

Khi cluster đã có controller, ví dụ Traefik, tạo file môi trường riêng:

```yaml
ingress:
  enabled: true
  className: traefik
  host: bankops.example.com
  tls:
    enabled: false
frontend:
  scheme: http
```

Thêm file đó vào lệnh Helm bằng `-f`. Controller cần được expose ra EC2 và DNS phải trỏ đúng. Để bật TLS, cấu hình `ingress.tls.enabled: true`, `ingress.tls.secretName` và `frontend.scheme: https`. Secret TLS cần có sẵn hoặc được cert-manager cấp theo annotation; chart không tự cài controller, cert-manager hoặc cấp chứng chỉ.

## 9. Kiểm tra chart khi phát triển

Trên laptop có Helm và PyYAML:

```bash
python3 -m unittest discover -s apps/bank-of-anthos/tests -v
```

Các kiểm tra render xác minh inventory, selector, image/probe, loại tham chiếu ConfigMap/Secret, PVC, override, NodePort/Ingress/TLS và checksum. Kiểm tra script tiếp nhận dùng lệnh Kubernetes giả lập để xác nhận chế độ chỉ đọc, chặn owner khác và không tiếp nhận PVC/Secret.

Đóng gói nếu cần:

```bash
helm package ./apps/bank-of-anthos/chart --destination /tmp
```

Thay đổi app sau khi chuyển thành công qua chart/values và `helm upgrade`. Bộ manifest thủ công tiếp tục là mốc tham khảo. Một Helm rollback chỉ quay cấu hình release, không phục hồi dữ liệu/database migration hoặc Secret bên ngoài release.

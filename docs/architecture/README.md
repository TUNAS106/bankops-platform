# BankOps High-Level Architecture

Mở [bankops-architecture.drawio](bankops-architecture.drawio) bằng Draw.io Integration trong VS Code. File chỉ có một trang; các khối dùng Sketch Style và logo SVG nhúng, chỉnh sửa được.

![BankOps architecture](system-architecture.svg)

Chèn vào README root:

```markdown
![BankOps architecture](docs/architecture/system-architecture.svg)
```

Sau khi sửa Draw.io, export lại SVG để đồng bộ bản README.

## Phạm vi

- Registry thực tế là Docker Hub, không phải ECR.
- Jenkins tạo PR cập nhật image; người vận hành merge, Argo CD triển khai. Đường Docker Hub → GitOps thể hiện image reference, không phải registry tự ghi Git.
- Prometheus/Grafana là workload trong cluster; vị trí bên cạnh cluster thể hiện vai trò hỗ trợ, không phải EC2 riêng.
- EC2 Spot: một control plane, hai worker; Jenkins là EC2 riêng.
- Longhorn replica trên EBS không thay thế backup. Đã có thao tác phục hồi EBS snapshot; repo chưa có cấu hình backup tự động được xác minh.
- Trivy hiện report-only đối với CVE. TLS ứng dụng dùng CA nội bộ và kết thúc tại Traefik.
- Bố cục mô tả nhóm logic, không cố định Pod vào node.

Logo: Simple Icons (16.0.0; Slack 11.15.0), CNCF artwork (Longhorn, cert-manager). Biểu tượng EC2/EBS/developer là hình minh họa. Nhãn hiệu thuộc các chủ sở hữu tương ứng.

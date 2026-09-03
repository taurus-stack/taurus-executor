# Taurus Executor 部署指南

## 概述

Taurus Executor 是一款高性能的远程命令执行系统。本指南涵盖了从注册到生产部署的完整部署流程。

---

## 系统架构

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Taurus Server   │◄────────│  Taurus Executor  │────────►│  目标主机         │
│  (后端)          │  心跳/注│  (Agent 进程)     │ 执行命令 │  (远程主机)       │
│                 │   册/注  │                   │         │                 │
└─────────────────┘         └──────────────────┘         └─────────────────┘
```

### 组件

| 组件 | 路径 | 用途 |
|------|------|------|
| 执行器核心 | `src/executor_core/` | 命令执行的主进程 |
| 客户端 SDK | `manage/sdk/` | 连接执行器的 Python 库 |
| CLI 工具 | `manage/cli.py` | 命令行界面 |
| Web UI | `web_ui.py` | 基于 Web 的管理界面 |

### 架构优势

1. **零停机更新**：新版本在独立端口启动后再切换
2. **自动恢复**：进程失败时自动重启
3. **版本管理**：多个版本可共存，便于快速回滚
4. **双向 TLS 安全**：所有通信使用双向 TLS

---

## 前置条件

- Python 3.12+
- Poetry（用于依赖管理）
- OpenSSL（用于证书管理）
- systemd（用于生产部署）

---

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/taurus-stack/taurus-executor.git
cd taurus-executor
```

### 2. 安装依赖

```bash
poetry install
```

### 3. 生成 gRPC 代码

```bash
make build-all
```

### 4. 配置环境

```bash
cp .env.example .env
```

编辑 `.env` 并填入你的配置：

```bash
TAURUS_SERVER_URL=http://your-server:8000
TAURUS_HOST_UUID=your-host-uuid

# TLS 配置
TLS_SERVER_CERT_PATH=./tls/server.crt
TLS_SERVER_KEY_PATH=./tls/server.key
TLS_CA_PATH=./tls/ca.crt

# gRPC 配置
GRPC_HOST=0.0.0.0
GRPC_PORT=50051
```

---

## TLS 证书管理

### 证书架构

Taurus Executor 使用双向 TLS（mutual TLS）进行安全通信：

```
Taurus Server（持有 ca.key）
├── 签发客户端证书
├── 管理证书信任
└── 证书吊销/续期

Taurus Executor（不持有 ca.key）
├── ca.crt                    # 验证服务端证书
├── client.crt / client.key   # 双向 TLS 认证
└── .env                      # 配置
```

### 获取证书

#### 方案 1：自动注册（推荐）

向服务端注册时，自动签发证书：

```bash
./scripts/register.sh --server http://<taurus-server>:8000 --token <your-token>
```

这将创建：
- `~/.taurus-executor/tls/ca.crt` - CA 根证书
- `~/.taurus-executor/tls/client.crt` - 客户端证书
- `~/.taurus-executor/tls/client.key` - 客户端私钥

#### 方案 2：手动证书请求

1. 生成客户端密钥和 CSR：
```bash
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=<hostname>"
```

2. 将 CSR 提交到服务端 API：
```bash
curl -X POST http://<taurus-server>:8000/api/taurus/executor/certificate/ \
  -H "Content-Type: application/json" \
  -d '{"host_id": "<host_id>", "csr": "<CSR_CONTENT>"}'
```

3. 将返回的证书保存到 `tls/` 目录

### 证书续期

1. **自动**：客户端检测即将过期的证书并请求续期
2. **手动**：管理员从服务端重新签发证书
3. **吊销**：管理员可吊销特定的客户端证书

---

## 运行执行器

### 开发模式

```bash
# 启动执行器
poetry run client

# 或直接使用 Python
python -m executor_core.main
```

### 生产模式（systemd）

1. 创建 systemd 服务文件：

```bash
sudo tee /etc/systemd/system/taurus-executor.service > /dev/null << 'EOF'
[Unit]
Description=Taurus Executor Agent
After=network.target

[Service]
Type=simple
User=taurus
Group=taurus
WorkingDirectory=/opt/taurus-executor
ExecStart=/opt/taurus-executor/taurus-executor
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# 安全
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/taurus-executor/data /opt/taurus-executor/logs

[Install]
WantedBy=multi-user.target
EOF
```

2. 安装执行器：

```bash
# 创建用户和目录
sudo useradd -r -s /sbin/nologin taurus
sudo mkdir -p /opt/taurus-executor/{data,logs,tls}
sudo chown -R taurus:taurus /opt/taurus-executor

# 复制二进制文件和配置
sudo cp dist/taurus-executor /opt/taurus-executor/
sudo cp .env /opt/taurus-executor/
sudo cp -r tls/* /opt/taurus-executor/tls/

# 启用并启动服务
sudo systemctl daemon-reload
sudo systemctl enable taurus-executor
sudo systemctl start taurus-executor
```

3. 检查状态：

```bash
sudo systemctl status taurus-executor
sudo journalctl -u taurus-executor -f
```

---

## Docker 部署

### 使用 Docker Compose

```bash
# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f taurus-executor

# 停止
docker-compose down
```

### 直接使用 Docker

```bash
# 构建镜像
docker build -t taurus-executor:latest .

# 运行容器
docker run -d \
  --name taurus-executor \
  --restart unless-stopped \
  -p 50051:50051 \
  -v $(pwd)/tls:/opt/taurus-executor/tls:ro \
  -v $(pwd)/data:/opt/taurus-executor/data \
  -e TAURUS_SERVER_URL=http://your-server:8000 \
  -e TAURUS_HOST_UUID=your-host-uuid \
  taurus-executor:latest
```

---

## 打包为二进制

### 使用 PyInstaller

```bash
# 打包执行器
make package

# 或手动执行
bash scripts/package.sh
```

这将在 `dist/taurus-executor` 创建一个独立的二进制文件。

### 包结构

```
dist/
└── taurus-executor          # 独立二进制文件
```

---

## 健康监控

### 健康检查端点

执行器暴露一个健康检查端点：

```bash
curl http://localhost:50051/health
```

### Prometheus 指标

指标可在以下地址获取：

```bash
curl http://localhost:50051/metrics
```

可用指标：
- `executor_commands_total`：已执行命令总数
- `executor_commands_failed_total`：失败命令计数
- `executor_command_duration_seconds`：命令执行时长
- `executor_sessions_active`：活跃会话数
- `executor_cpu_usage_percent`：CPU 使用率
- `executor_memory_usage_bytes`：内存使用量

---

## 故障排查

### 常见问题

#### 1. 证书错误

```
Error: TLS handshake failed
```

**解决方案**：验证证书是否有效且配置正确：
```bash
openssl x509 -in tls/client.crt -text -noout
openssl verify -CAfile tls/ca.crt tls/client.crt
```

#### 2. 连接被拒绝

```
Error: Connection refused
```

**解决方案**：检查执行器是否正在运行：
```bash
sudo systemctl status taurus-executor
netstat -tlnp | grep 50051
```

#### 3. 权限被拒绝

```
Error: Permission denied
```

**解决方案**：验证文件权限：
```bash
ls -la tls/
sudo chown -R taurus:taurus /opt/taurus-executor
```

### 日志

```bash
# systemd 日志
sudo journalctl -u taurus-executor -f

# Docker 日志
docker logs -f taurus-executor

# 文件日志（如已配置）
tail -f /opt/taurus-executor/logs/executor.log
```

---

## 升级

### 零停机升级

执行器支持自动零停机升级：

1. 新版本下载包
2. 验证 SHA256 校验和
3. 安装到独立目录
4. 在不同端口启动新版本
5. 对新版本进行健康检查
6. 切换流量并停止旧版本

### 手动升级

```bash
# 停止当前版本
sudo systemctl stop taurus-executor

# 备份当前版本
sudo cp /opt/taurus-executor/taurus-executor /opt/taurus-executor/taurus-executor.bak

# 安装新版本
sudo cp dist/taurus-executor /opt/taurus-executor/

# 启动新版本
sudo systemctl start taurus-executor

# 验证
sudo systemctl status taurus-executor
```

### 回滚

```bash
# 停止当前版本
sudo systemctl stop taurus-executor

# 恢复备份
sudo mv /opt/taurus-executor/taurus-executor.bak /opt/taurus-executor/taurus-executor

# 启动恢复后的版本
sudo systemctl start taurus-executor
```

---

## 安全最佳实践

1. **始终使用双向 TLS**：为所有通信启用双向 TLS
2. **轮换证书**：定期轮换 TLS 证书
3. **限制网络暴露**：仅将 gRPC 端口暴露给受信任的网络
4. **使用防火墙规则**：使用 iptables/firewalld 限制访问
5. **监控日志**：定期检查日志以发现可疑活动
6. **保持更新**：始终运行最新版本
7. **审计权限**：定期审计命令执行权限
8. **保护私钥**：使用受限权限存储私钥

```bash
# 设置正确的权限
chmod 600 tls/*.key
chmod 644 tls/*.crt
chown -R taurus:taurus tls/
```
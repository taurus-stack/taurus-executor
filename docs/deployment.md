# Taurus Executor Deployment Guide

## Overview

Taurus Executor is a high-performance remote command execution system. This guide covers the complete deployment process from registration to production deployment.

---

## System Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Taurus Server   │◄────────│  Taurus Executor  │────────►│  Target Host    │
│  (Backend)      │  Heart-  │  (Agent Process)  │ Execute │  (Remote Host)  │
│                 │ beat/Reg│                   │ Commands│                 │
└─────────────────┘         └──────────────────┘         └─────────────────┘
```

### Components

| Component | Path | Purpose |
|-----------|------|---------|
| Executor Core | `src/executor_core/` | Main process for command execution |
| Client SDK | `manage/sdk/` | Python library for connecting to executors |
| CLI Tool | `manage/cli.py` | Command-line interface |
| Web UI | `web_ui.py` | Web-based management interface |

### Architecture Benefits

1. **Zero-Downtime Updates**: New versions start on independent ports before switching
2. **Auto Recovery**: Automatic restart on process failure
3. **Version Management**: Multiple versions can coexist for quick rollback
4. **mTLS Security**: Mutual TLS for all communications

---

## Prerequisites

- Python 3.12+
- Poetry (for dependency management)
- OpenSSL (for certificate management)
- systemd (for production deployment)

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/taurus-stack/taurus-executor.git
cd taurus-executor
```

### 2. Install Dependencies

```bash
poetry install
```

### 3. Generate gRPC Code

```bash
make build-all
```

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
TAURUS_SERVER_URL=http://your-server:8000
TAURUS_HOST_UUID=your-host-uuid

# TLS Configuration
TLS_SERVER_CERT_PATH=./tls/server.crt
TLS_SERVER_KEY_PATH=./tls/server.key
TLS_CA_PATH=./tls/ca.crt

# gRPC Configuration
GRPC_HOST=0.0.0.0
GRPC_PORT=50051
```

---

## TLS Certificate Management

### Certificate Architecture

Taurus Executor uses mTLS (mutual TLS) for secure communication:

```
Taurus Server (holds ca.key)
├── Issues client certificates
├── Manages certificate trust
└── Certificate revocation/renewal

Taurus Executor (does not hold ca.key)
├── ca.crt                    # Verify server certificate
├── client.crt / client.key   # mTLS authentication
└── .env                      # Configuration
```

### Obtaining Certificates

#### Option 1: Automatic Registration (Recommended)

When registering with the server, certificates are automatically issued:

```bash
./scripts/register.sh --server http://<taurus-server>:8000 --token <your-token>
```

This creates:
- `~/.taurus-executor/tls/ca.crt` - CA root certificate
- `~/.taurus-executor/tls/client.crt` - Client certificate
- `~/.taurus-executor/tls/client.key` - Client private key

#### Option 2: Manual Certificate Request

1. Generate client key and CSR:
```bash
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=taurus-stack/CN=<hostname>"
```

2. Submit CSR to server API:
```bash
curl -X POST http://<taurus-server>:8000/api/taurus/executor/certificate/ \
  -H "Content-Type: application/json" \
  -d '{"host_id": "<host_id>", "csr": "<CSR_CONTENT>"}'
```

3. Save returned certificates to `tls/` directory

### Certificate Renewal

1. **Automatic**: Client detects expiring certificates and requests renewal
2. **Manual**: Administrator reissues certificates from server
3. **Revocation**: Administrator can revoke specific client certificates

---

## Running the Executor

### Development Mode

```bash
# Start the executor
poetry run client

# Or using Python directly
python -m executor_core.main
```

### Production Mode (systemd)

1. Create systemd service file:

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

# Security
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/taurus-executor/data /opt/taurus-executor/logs

[Install]
WantedBy=multi-user.target
EOF
```

2. Install the executor:

```bash
# Create user and directories
sudo useradd -r -s /sbin/nologin taurus
sudo mkdir -p /opt/taurus-executor/{data,logs,tls}
sudo chown -R taurus:taurus /opt/taurus-executor

# Copy binary and configuration
sudo cp dist/taurus-executor /opt/taurus-executor/
sudo cp .env /opt/taurus-executor/
sudo cp -r tls/* /opt/taurus-executor/tls/

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable taurus-executor
sudo systemctl start taurus-executor
```

3. Check status:

```bash
sudo systemctl status taurus-executor
sudo journalctl -u taurus-executor -f
```

---

## Docker Deployment

### Using Docker Compose

```bash
# Start with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f taurus-executor

# Stop
docker-compose down
```

### Using Docker Directly

```bash
# Build image
docker build -t taurus-executor:latest .

# Run container
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

## Packaging as Binary

### Using PyInstaller

```bash
# Package the executor
make package

# Or manually
bash scripts/package.sh
```

This creates a standalone binary in `dist/taurus-executor`.

### Package Structure

```
dist/
└── taurus-executor          # Standalone binary
```

---

## Health Monitoring

### Health Check Endpoint

The executor exposes a health check endpoint:

```bash
curl http://localhost:50051/health
```

### Prometheus Metrics

Metrics are available at:

```bash
curl http://localhost:50051/metrics
```

Available metrics:
- `executor_commands_total`: Total commands executed
- `executor_commands_failed_total`: Failed commands count
- `executor_command_duration_seconds`: Command execution duration
- `executor_sessions_active`: Active sessions count
- `executor_cpu_usage_percent`: CPU usage
- `executor_memory_usage_bytes`: Memory usage

---

## Troubleshooting

### Common Issues

#### 1. Certificate Errors

```
Error: TLS handshake failed
```

**Solution**: Verify certificates are valid and correctly configured:
```bash
openssl x509 -in tls/client.crt -text -noout
openssl verify -CAfile tls/ca.crt tls/client.crt
```

#### 2. Connection Refused

```
Error: Connection refused
```

**Solution**: Check if the executor is running:
```bash
sudo systemctl status taurus-executor
netstat -tlnp | grep 50051
```

#### 3. Permission Denied

```
Error: Permission denied
```

**Solution**: Verify file permissions:
```bash
ls -la tls/
sudo chown -R taurus:taurus /opt/taurus-executor
```

### Logs

```bash
# systemd logs
sudo journalctl -u taurus-executor -f

# Docker logs
docker logs -f taurus-executor

# File logs (if configured)
tail -f /opt/taurus-executor/logs/executor.log
```

---

## Upgrading

### Zero-Downtime Upgrade

The executor supports automatic zero-downtime upgrades:

1. New version downloads package
2. Verifies SHA256 checksum
3. Installs to independent directory
4. Starts new version on different port
5. Health checks new version
6. Switches traffic and stops old version

### Manual Upgrade

```bash
# Stop current version
sudo systemctl stop taurus-executor

# Backup current version
sudo cp /opt/taurus-executor/taurus-executor /opt/taurus-executor/taurus-executor.bak

# Install new version
sudo cp dist/taurus-executor /opt/taurus-executor/

# Start new version
sudo systemctl start taurus-executor

# Verify
sudo systemctl status taurus-executor
```

### Rollback

```bash
# Stop current version
sudo systemctl stop taurus-executor

# Restore backup
sudo mv /opt/taurus-executor/taurus-executor.bak /opt/taurus-executor/taurus-executor

# Start restored version
sudo systemctl start taurus-executor
```

---

## Security Best Practices

1. **Always use mTLS**: Enable mutual TLS for all communications
2. **Rotate certificates**: Regularly rotate TLS certificates
3. **Limit network exposure**: Only expose gRPC port to trusted networks
4. **Use firewall rules**: Restrict access using iptables/firewalld
5. **Monitor logs**: Regularly review logs for suspicious activity
6. **Keep updated**: Always run the latest version
7. **Audit permissions**: Regularly audit command execution permissions
8. **Secure private keys**: Store private keys with restricted permissions

```bash
# Set proper permissions
chmod 600 tls/*.key
chmod 644 tls/*.crt
chown -R taurus:taurus tls/
```
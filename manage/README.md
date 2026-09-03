# Taurus Client SDK

A Python client library and CLI for the Taurus Executor system.

## Installation

```bash
pip install .
```

Or for development:

```bash
pip install -e .
```

## Usage

### Basic command execution

```bash
# Execute a simple command
python -m manage.cli --address localhost:50051 exec ls -la

# Execute with timeout
python -m manage.cli --address localhost:50051 exec --timeout 60 ps aux
```

### Get client status

```bash
python -m manage.cli --address localhost:50051 status
```

### Specify client role

```bash
# Execute command with admin role
python -m manage.cli --address localhost:50051 --role admin exec systemctl status nginx

# Get status with operator role
python -m manage.cli --address localhost:50051 --role operator status
```

### Secure connections (mTLS)

```bash
python -m manage.cli \
  --address localhost:50051 \
  --cert-file ./tls/client.crt \
  --key-file ./tls/client.key \
  --ca-file ./tls/ca.crt \
  exec systemctl status nginx
```

### Privileged command execution

To execute commands that require elevated privileges:

```bash
# Execute with sudo password prompt
python -m manage.cli --address localhost:50051 exec --privileged systemctl restart nginx

# Execute as specific user (requires NOPASSWD sudo configuration)
python -m manage.cli --address localhost:50051 exec --privileged --sudo-user www-data whoami

# Switch to another user using su (requires password for target user)
python -m manage.cli --address localhost:50051 exec --privileged --su-user appuser whoami
```

When using the `--privileged` flag, the client will prompt for a sudo password which will be securely transmitted to the client.

When using the `--su-user` flag, the client will prompt for the target user's password.

**Warning**: Password transmission should only be used with secure connections (mTLS).

## Configuration

The client looks for TLS certificates in the `tls/` directory by default:
- `tls/client.crt`: Client certificate
- `tls/client.key`: Client private key
- `tls/ca.crt`: CA certificate for server verification

These can be overridden with command-line flags.

## Dependencies

- Python 3.9+
- grpcio
- rich (optional, for enhanced terminal UI)

Install rich for a better experience:

```bash
pip install rich
```
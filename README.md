# Taurus Executor

A high-performance, self-updating remote command execution system built with gRPC.

## Features

- **Remote Command Execution**: Execute commands on remote hosts with real-time streaming output
- **Session Support**: Interactive shell sessions with stateful command execution
- **File Transfer**: Upload and download files between client and server
- **mTLS Security**: Mutual TLS authentication for secure communication
- **Self-Updating**: Zero-downtime automatic updates with health checks
- **Privileged Execution**: Support for sudo and su-based command execution
- **Cross-Platform**: Works on Linux and macOS

## Quick Start

### Prerequisites

- Python 3.12+
- Poetry (for dependency management)

### Installation

```bash
# Clone the repository
git clone https://github.com/taurus-stack/taurus-executor.git
cd taurus-executor

# Install dependencies
poetry install

# Generate gRPC code
make build
```

### Configuration

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
TAURUS_SERVER_URL=http://your-server:8000
TAURUS_HOST_UUID=your-host-uuid

# TLS Configuration
TLS_SERVER_CERT_PATH=/path/to/server.crt
TLS_SERVER_KEY_PATH=/path/to/server.key
TLS_CA_PATH=/path/to/ca.crt

# gRPC Configuration
GRPC_HOST=0.0.0.0
GRPC_PORT=50051

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### Running

```bash
# Start the executor
poetry run client

# Or using Python directly
python -m executor_core.main
```

## Project Structure

```
taurus-executor/
├── src/
│   └── executor_core/
│       ├── main.py              # Entry point
│       ├── executors/           # Command execution engines
│       ├── services/            # gRPC services and handlers
│       └── infra/               # Infrastructure (config, TLS, logging)
├── manage/                      # SDK and CLI tools
│   ├── sdk/                     # Python client SDK
│   └── cli.py                   # Command-line interface
├── proto/                       # Protocol buffer definitions
├── scripts/                     # Build and utility scripts
├── tests/                       # Unit and integration tests
└── examples/                    # Usage examples
```

## Usage

### Using the SDK

```python
from manage.sdk.client import TaurusClient

async def main():
    async with TaurusClient("localhost:50051") as client:
        # Execute a command
        async for event in client.execute_command("ls", ["-la"]):
            if "stdout" in event:
                print(event["stdout"].decode())
            if "finished" in event:
                print(f"Exit code: {event['exit_code']}")

        # Get system status
        status = await client.get_status()
        print(f"Hostname: {status['hostname']}")
        print(f"CPU Usage: {status['cpu_usage']}%")
        print(f"Memory Usage: {status['memory_usage']}%")

import asyncio
asyncio.run(main())
```

### Using the CLI

```bash
# Execute a command
python -m manage.cli --address localhost:50051 exec ls -la

# Get status
python -m manage.cli --address localhost:50051 status

# With TLS
python -m manage.cli \
  --address localhost:50051 \
  --cert client.crt \
  --key client.key \
  --ca ca.crt \
  exec ls -la
```

### Interactive Sessions

```python
from manage.sdk.client import TaurusClient

async def main():
    async with TaurusClient("localhost:50051") as client:
        # Create a session
        session = await client.create_session(
            username="user",
            password="password",
            working_directory="/home/user",
        )
        
        try:
            # Execute commands in the session
            async for event in session.execute("ls -la"):
                if "stdout" in event:
                    print(event["stdout"].decode())
        finally:
            await session.close()

import asyncio
asyncio.run(main())
```

## Building

### Generate gRPC Code

```bash
# Server-side code
make build

# SDK code
make build-sdk

# Both
make build-all
```

### Package as Binary

```bash
# Package with PyInstaller
make package

# Package with specific version
make package -- --version 1.0.0
```

## Testing

```bash
# Run all tests
make test

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/
```

## Code Quality

```bash
# Format code
make format

# Run linters
make lint
```

## Architecture

Taurus Executor consists of several key components:

- **gRPC Server**: Handles incoming command execution requests
- **Command Executor**: Manages process creation, I/O streaming, and lifecycle
- **Session Manager**: Maintains stateful interactive sessions
- **TLS Manager**: Handles certificate management and mTLS
- **State Manager**: Persists configuration and state across restarts
- **Self Updater**: Manages zero-downtime updates

See [docs/architecture.md](docs/architecture.md) for detailed architecture documentation.

## Deployment

For deployment instructions, see [docs/deployment.md](docs/deployment.md).

### Quick Deploy

```bash
# On target host
./scripts/register.sh --server http://taurus-server:8000 --token your-token

# Start as systemd service
sudo systemctl enable taurus-executor
sudo systemctl start taurus-executor
```

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TAURUS_SERVER_URL` | Server URL for registration | - |
| `TAURUS_HOST_UUID` | Unique host identifier | - |
| `GRPC_HOST` | gRPC listen address | `0.0.0.0` |
| `GRPC_PORT` | gRPC listen port | `50051` |
| `TLS_SERVER_CERT_PATH` | Server certificate path | - |
| `TLS_SERVER_KEY_PATH` | Server key path | - |
| `TLS_CA_PATH` | CA certificate path | - |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | Log format (text/json) | `text` |

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `make test`
5. Run linters: `make lint`
6. Submit a pull request

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/taurus-stack/taurus-executor/issues)
- **Documentation**: [docs/](docs/)
- **Examples**: [examples/](examples/)
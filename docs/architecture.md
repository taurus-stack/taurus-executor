# Taurus Executor Architecture

## Overview

Taurus Executor is a high-performance, self-updating remote command execution system built with gRPC. It enables secure, real-time command execution on remote hosts with support for interactive sessions, file transfers, and privileged operations.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Taurus Server                                │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Management API                             │   │
│  │  - Host Registration    - Command Dispatch                    │   │
│  │  - Package Management   - Health Monitoring                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ gRPC (mTLS)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Taurus Executor                                │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    gRPC Server                                │   │
│  │  - ClientService      - Command Execution                    │   │
│  │  - Session Management - File Transfer                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Command Executor                           │   │
│  │  - Process Management  - I/O Streaming                       │   │
│  │  - Signal Handling     - Timeout Management                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Session Manager                            │   │
│  │  - Interactive Shells  - Stateful Commands                   │   │
│  │  - Working Directory   - Environment Variables               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Infrastructure                             │   │
│  │  - TLS Manager         - Config Management                   │   │
│  │  - State Manager       - Logging                             │   │
│  │  - Self Updater        - Permissions                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. gRPC Server (`services/server.py`)

The gRPC server is the main entry point for the executor. It handles:

- **mTLS Authentication**: Mutual TLS for secure client-server communication
- **Command Execution**: ExecuteCommand RPC for running commands
- **Session Management**: CreateSession, ExecuteInSession, CloseSession RPCs
- **File Transfer**: UploadFile, DownloadFile RPCs
- **Health Checks**: HealthCheck RPC for monitoring
- **Self-Update**: Update RPC for triggering updates

### 2. Command Executor (`executors/command_executor.py`)

Handles the actual command execution:

- **Process Creation**: Uses `subprocess.Popen` for process creation
- **I/O Streaming**: Async reading of stdout/stderr
- **Signal Handling**: Proper signal forwarding to child processes
- **Timeout Management**: Command timeout with graceful termination
- **Exit Code Tracking**: Proper exit code and signal reporting

### 3. Session Manager (`services/session_manager.py`)

Manages interactive shell sessions:

- **Session Creation**: Creates persistent shell sessions
- **Command Execution**: Executes commands within session context
- **Working Directory**: Maintains working directory across commands
- **Environment Variables**: Preserves environment variables
- **Session Cleanup**: Proper cleanup on session close

### 4. TLS Manager (`infra/tls_manager.py`)

Handles certificate management:

- **Certificate Loading**: Loads server/client certificates and keys
- **mTLS Configuration**: Configures mutual TLS for gRPC
- **CRL Checking**: Certificate Revocation List validation
- **Certificate Generation**: Helper functions for cert generation

### 5. State Manager (`infra/state_manager.py`)

Persists configuration and state:

- **Configuration Storage**: Stores executor configuration
- **State Persistence**: Persists state across restarts
- **Atomic Writes**: Ensures atomic state file writes
- **Migration Support**: Handles state format migrations

### 6. Self Updater (`infra/self_updater.py`)

Manages zero-downtime updates:

- **Version Checking**: Periodic check for new versions
- **Package Download**: Downloads new version packages
- **Checksum Verification**: SHA256 verification of downloads
- **Graceful Switch**: Starts new version before stopping old
- **Health Check**: Validates new version before switching
- **Rollback Support**: Automatic rollback on failure

## Data Flow

### Command Execution Flow

```
1. Client sends ExecuteCommand request
2. gRPC Server receives request
3. Auth Interceptor validates request
4. Command Executor creates subprocess
5. I/O streams are read asynchronously
6. CommandResponse events are streamed back
7. Process exits, final response sent
```

### Session Flow

```
1. Client sends CreateSession request
2. Session Manager creates shell session
3. Session ID returned to client
4. Client sends ExecuteInSession requests
5. Commands executed in session context
6. Client sends CloseSession request
7. Session Manager cleans up resources
```

### Self-Update Flow

```
1. Updater checks for new version
2. Downloads package from server
3. Verifies SHA256 checksum
4. Installs to new directory
5. Starts new version process
6. Health check on new process
7. Stops old process if healthy
8. Rolls back if unhealthy
```

## Security Model

### Authentication

- **mTLS**: Mutual TLS for all gRPC communications
- **Ticket-based Auth**: Temporary tickets for session access
- **Certificate Revocation**: CRL checking for revoked certs

### Authorization

- **Role-based Access**: Admin, Operator, Viewer roles
- **Command Validation**: Validates command parameters
- **Permission Checks**: Checks file system permissions

### Data Protection

- **Encrypted Transport**: All data encrypted via TLS
- **Secure Password Handling**: Passwords transmitted securely
- **Audit Logging**: All commands logged for audit

## Configuration

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
| `UPDATE_CHECK_INTERVAL_SECONDS` | Update check interval | `3600` |
| `CURRENT_VERSION` | Current version | `0.1.0` |

## Directory Structure

```
/opt/taurus-executor/
├── taurus-executor              # Binary executable
├── .env                      # Configuration
├── tls/                      # TLS certificates
│   ├── ca.crt
│   ├── server.crt
│   └── server.key
├── data/                     # Runtime data
│   └── state.json
├── logs/                     # Log files
│   └── executor.log
└── versions/                 # Update versions
    └── v1.1.0/
        ├── taurus-executor
        └── .env
```

## Protocol Buffers

The gRPC service is defined in `proto/executor/v1/command_service.proto`:

- **ClientService**: Main service for command execution
- **ExecuteCommand**: Execute a command with streaming output
- **CreateSession**: Create an interactive session
- **ExecuteInSession**: Execute command in session
- **CloseSession**: Close a session
- **UploadFile**: Upload a file to the host
- **DownloadFile**: Download a file from the host
- **HealthCheck**: Check executor health
- **GetStatus**: Get system status

## Performance Considerations

- **Async I/O**: All I/O operations are asynchronous
- **Connection Pooling**: gRPC connection pooling for efficiency
- **Memory Management**: Proper cleanup of subprocess resources
- **Buffer Management**: Efficient buffer handling for I/O streams
- **Process Isolation**: Each command runs in isolated process

## Error Handling

- **Graceful Degradation**: System continues operating on partial failures
- **Retry Logic**: Automatic retry for transient failures
- **Circuit Breaker**: Prevents cascading failures
- **Health Monitoring**: Continuous health checking
- **Alert Integration**: Alerts on critical failures
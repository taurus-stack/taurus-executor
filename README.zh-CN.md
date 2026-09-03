# Taurus Executor

一款基于 gRPC 构建的高性能、自动更新的远程命令执行系统。

## 功能特性

- **远程命令执行**：在远程主机上执行命令，支持实时流式输出
- **会话支持**：交互式 Shell 会话，支持有状态的命令执行
- **文件传输**：在客户端和服务器之间上传和下载文件
- **mTLS 安全**：双向 TLS 认证，确保通信安全
- **自动更新**：零停机自动更新，附带健康检查
- **特权执行**：支持 sudo 和 su 方式执行命令
- **跨平台**：支持 Linux 和 macOS

## 快速开始

### 前置条件

- Python 3.12+
- Poetry（用于依赖管理）

### 安装

```bash
# 克隆仓库
git clone https://github.com/taurus-stack/taurus-executor.git
cd taurus-executor

# 安装依赖
poetry install

# 生成 gRPC 代码
make build
```

### 配置

复制示例环境文件并进行配置：

```bash
cp .env.example .env
```

编辑 `.env` 并填入你的配置：

```bash
TAURUS_SERVER_URL=http://your-server:8000
TAURUS_HOST_UUID=your-host-uuid

# TLS 配置
TLS_SERVER_CERT_PATH=/path/to/server.crt
TLS_SERVER_KEY_PATH=/path/to/server.key
TLS_CA_PATH=/path/to/ca.crt

# gRPC 配置
GRPC_HOST=0.0.0.0
GRPC_PORT=50051

# 日志
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### 运行

```bash
# 启动执行器
poetry run client

# 或直接使用 Python
python -m executor_core.main
```

## 项目结构

```
taurus-executor/
├── src/
│   └── executor_core/
│       ├── main.py              # 程序入口
│       ├── executors/           # 命令执行引擎
│       ├── services/            # gRPC 服务与处理器
│       └── infra/               # 基础设施（配置、TLS、日志）
├── manage/                      # SDK 和 CLI 工具
│   ├── sdk/                     # Python 客户端 SDK
│   └── cli.py                   # 命令行界面
├── proto/                       # Protocol Buffer 定义
├── scripts/                     # 构建和实用脚本
├── tests/                       # 单元测试与集成测试
└── examples/                    # 使用示例
```

## 使用方法

### 使用 SDK

```python
from manage.sdk.client import TaurusClient

async def main():
    async with TaurusClient("localhost:50051") as client:
        # 执行命令
        async for event in client.execute_command("ls", ["-la"]):
            if "stdout" in event:
                print(event["stdout"].decode())
            if "finished" in event:
                print(f"退出码: {event['exit_code']}")

        # 获取系统状态
        status = await client.get_status()
        print(f"主机名: {status['hostname']}")
        print(f"CPU 使用率: {status['cpu_usage']}%")
        print(f"内存使用率: {status['memory_usage']}%")

import asyncio
asyncio.run(main())
```

### 使用 CLI

```bash
# 执行命令
python -m manage.cli --address localhost:50051 exec ls -la

# 获取状态
python -m manage.cli --address localhost:50051 status

# 使用 TLS
python -m manage.cli \
  --address localhost:50051 \
  --cert client.crt \
  --key client.key \
  --ca ca.crt \
  exec ls -la
```

### 交互式会话

```python
from manage.sdk.client import TaurusClient

async def main():
    async with TaurusClient("localhost:50051") as client:
        # 创建会话
        session = await client.create_session(
            username="user",
            password="password",
            working_directory="/home/user",
        )

        try:
            # 在会话中执行命令
            async for event in session.execute("ls -la"):
                if "stdout" in event:
                    print(event["stdout"].decode())
        finally:
            await session.close()

import asyncio
asyncio.run(main())
```

## 构建

### 生成 gRPC 代码

```bash
# 服务端代码
make build

# SDK 代码
make build-sdk

# 全部
make build-all
```

### 打包为二进制

```bash
# 使用 PyInstaller 打包
make package

# 指定版本打包
make package -- --version 1.0.0
```

## 测试

```bash
# 运行全部测试
make test

# 仅运行单元测试
pytest tests/unit/

# 仅运行集成测试
pytest tests/integration/
```

## 代码质量

```bash
# 格式化代码
make format

# 运行代码检查
make lint
```

## 架构说明

Taurus Executor 由以下几个关键组件组成：

- **gRPC 服务端**：处理传入的命令执行请求
- **命令执行器**：管理进程创建、I/O 流式传输和生命周期
- **会话管理器**：维护有状态的交互式会话
- **TLS 管理器**：处理证书管理和双向 TLS
- **状态管理器**：在重启后保持配置和状态
- **自动更新器**：管理零停机更新

详细的架构文档请参考 [docs/architecture.md](docs/architecture.md)。

## 部署

部署说明请参考 [docs/deployment.md](docs/deployment.md)。

### 快速部署

```bash
# 在目标主机上
./scripts/register.sh --server http://taurus-server:8000 --token your-token

# 以 systemd 服务启动
sudo systemctl enable taurus-executor
sudo systemctl start taurus-executor
```

## 配置参考

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TAURUS_SERVER_URL` | 用于注册的服务端 URL | - |
| `TAURUS_HOST_UUID` | 唯一主机标识符 | - |
| `GRPC_HOST` | gRPC 监听地址 | `0.0.0.0` |
| `GRPC_PORT` | gRPC 监听端口 | `50051` |
| `TLS_SERVER_CERT_PATH` | 服务端证书路径 | - |
| `TLS_SERVER_KEY_PATH` | 服务端密钥路径 | - |
| `TLS_CA_PATH` | CA 证书路径 | - |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FORMAT` | 日志格式（text/json） | `text` |

## 贡献指南

1. Fork 本仓库
2. 创建功能分支
3. 进行修改
4. 运行测试：`make test`
5. 运行代码检查：`make lint`
6. 提交 Pull Request

## 许可证

本项目基于 MIT 许可证发布 - 详见 [LICENSE](LICENSE) 文件。

## 支持

- **问题反馈**：[GitHub Issues](https://github.com/taurus-stack/taurus-executor/issues)
- **文档**：[docs/](docs/)
- **示例**：[examples/](examples/)
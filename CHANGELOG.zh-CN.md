# 变更日志

所有显著的更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
并且项目遵循 [语义化版本控制](https://semver.org/spec/v2.0.0.html)。

## [未发布]

### 新增
- 完整的 Python 客户端 SDK (`manage/sdk/`)
- 命令行界面 (`manage/cli.py`)
- 基于 Web 的管理界面 (`web_ui.py`)
- 支持 gRPC 双向 TLS 认证
- 命令执行支持实时流式输出
- 支持交互式 Shell 会话
- 文件上传和下载功能
- 零停机自动更新机制
- systemd 生产部署支持
- Docker 部署支持
- 通过 PyInstaller 打包为独立二进制文件
- 全面的单元测试和集成测试
- 性能基准测试框架
- 完整的架构和部署文档
- CI/CD GitHub Actions 工作流
- 贡献指南和安全策略
- 支持 sudo 和 su 的特权执行
- 会话级别的工作目录管理
- 会话级别的环境变量管理
- 健康检查端点
- Prometheus 指标暴露

### 已更改
- 从单一脚本重构为模块化架构
- 将 Protocol Buffer 定义移至 `proto/`
- 使用 Poetry 进行依赖管理
- 更新为新的 Taurus 命名约定

### 修复
- 处理长时间运行命令的超时问题
- 子进程的正确信号处理
- 会话终止时的资源泄漏
- 证书路径解析
- Windows 兼容性的路径处理

## [0.1.0] - 2026-06-28

### 新增
- 初始版本发布
- 基本命令执行功能
- gRPC 服务端实现
- 基本 TLS 支持
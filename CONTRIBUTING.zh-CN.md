# 贡献指南

感谢您对 Taurus Executor 项目的兴趣！本文档提供了贡献的指南和说明。

## 行为准则

参与本项目即表示您同意遵守我们的行为准则。请在所有互动中保持尊重和建设性的态度。

## 开始开发

### 开发环境

1. **Fork 并克隆** 仓库
2. **安装依赖**：
   ```bash
   poetry install --with dev
   ```
3. **生成 gRPC 代码**：
   ```bash
   make build-all
   ```
4. **运行测试** 确保一切正常：
   ```bash
   make test
   ```

### 开发工作流程

1. 从 `main` 创建功能分支：
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. 进行修改

3. **格式化代码**：
   ```bash
   make format
   ```

4. **运行代码检查**：
   ```bash
   make lint
   ```

5. **运行测试**：
   ```bash
   make test
   ```

6. 以清晰、描述性的提交消息提交更改

7. 推送到您的 fork 并提交 Pull Request

## Pull Request 指南

### 提交前检查

- [ ] 代码符合项目的风格指南
- [ ] 完成自我审查
- [ ] 必要处添加代码注释
- [ ] 为新功能添加测试
- [ ] 所有测试通过（`make test`）
- [ ] 代码检查通过（`make lint`）
- [ ] 必要时更新文档

### PR 描述

请包含以下内容：
- **什么** 更改
- **为什么** 进行这些更改
- **如何** 测试这些更改
- 任何 **破坏性更改** 或迁移说明

### 提交消息

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
type(scope): description

[可选的正文]

[可选的页脚]
```

类型：
- `feat`：新功能
- `fix`：Bug 修复
- `docs`：文档更改
- `style`：代码风格更改（格式化等）
- `refactor`：代码重构
- `test`：添加或更新测试
- `chore`：维护任务

示例：
```
feat(executor): 支持交互式会话
fix(sdk): 解决连接超时问题
docs(readme): 更新安装说明
test(handlers): 添加命令执行的单元测试
```

## 代码风格

我们使用：
- **Black** 进行代码格式化
- **isort** 进行导入排序
- **flake8** 进行代码检查
- **mypy** 进行类型检查

运行 `make format` 和 `make lint` 以确保合规。

## 测试

### 运行测试

```bash
# 全部测试
make test

# 仅单元测试
pytest tests/unit/

# 仅集成测试
pytest tests/integration/

# 特定测试文件
pytest tests/unit/test_executor.py

# 详细输出
pytest -v

# 覆盖率
pytest --cov=executor_core
```

### 编写测试

- 为所有新功能编写测试
- 单元测试应快速且隔离
- 集成测试应测试真实的 gRPC 通信
- 使用 `conftest.py` 中的 fixtures 进行通用设置

## 架构概览

Taurus Executor 由以下几个关键组件组成：

- **executor_core**：主要服务端实现
  - `executors/`：命令执行引擎
  - `services/`：gRPC 服务和处理器
  - `infra/`：基础设施（配置、TLS、日志、状态）
- **manage**：客户端 SDK 和 CLI 工具
  - `sdk/`：Python 客户端库
  - `cli.py`：命令行界面
- **proto**：Protocol Buffer 定义

## 报告问题

报告 Bug 时，请包含：

- **Taurus Executor 版本**
- **Python 版本**
- **操作系统**
- **复现步骤**
- **预期行为**
- **实际行为**
- **日志**（如适用）

## 功能请求

欢迎功能请求！请：

1. 检查现有问题以避免重复
2. 清晰描述使用场景
3. 解释为什么该功能有价值
4. 建议实现方案（可选）

## 文档

良好的文档至关重要。贡献时：

- 更新 `docs/` 中的相关文档
- 为新函数/类添加文档字符串
- 如涉及面向用户的更改，更新 README
- 为新功能包含示例

## 发布流程

发布由维护者管理。流程：

1. 在 `pyproject.toml` 中升级版本
2. 更新 `CHANGELOG.md`
3. 创建发布标签
4. 构建并发布到 PyPI
5. 创建包含二进制文件的 GitHub Release

## 有疑问？

- **一般问题**：开启 [Discussion](https://github.com/taurus-stack/taurus-executor/discussions)
- **Bug 报告**：开启 [Issue](https://github.com/taurus-stack/taurus-executor/issues)
- **代码审查**：提交 [Pull Request](https://github.com/taurus-stack/taurus-executor/pulls)

感谢您的贡献！🎉
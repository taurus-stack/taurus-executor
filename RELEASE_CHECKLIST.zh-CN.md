# 发布检查清单

## 发布前（Pre-Release）

### 代码审查
- [ ] 所有代码更改已通过 Pull Request 审查
- [ ] 没有遗留的调试代码或 TODO 注释
- [ ] 没有硬编码的密码或敏感信息
- [ ] 所有路径与平台无关（使用 `os.path.join` 或 `pathlib`）

### 测试
- [ ] 单元测试全部通过：`pytest tests/unit/`
- [ ] 集成测试全部通过：`pytest tests/integration/`
- [ ] 手动测试在 Linux 上完成
- [ ] 手动测试在 macOS 上完成
- [ ] 命令执行功能正常
- [ ] 会话管理功能正常
- [ ] 文件传输功能正常
- [ ] 自动更新功能正常
- [ ] TLS 认证功能正常

### 构建
- [ ] gRPC 代码已重新生成：`make build-all`
- [ ] 代码已格式化：`make format`
- [ ] Lint 检查通过：`make lint`
- [ ] 类型检查通过：`mypy src/`
- [ ] PyInstaller 打包成功：`make package`
- [ ] 二进制文件在干净环境中可运行
- [ ] Docker 镜像构建成功：`docker build -t taurus-executor:latest .`

### 文档
- [ ] README.md 已更新（英文）
- [ ] README.zh-CN.md 已更新（中文）
- [ ] CHANGELOG.md 已更新（英文）
- [ ] CHANGELOG.zh-CN.md 已更新（中文）
- [ ] docs/architecture.md 已更新（英文）
- [ ] docs/architecture.zh-CN.md 已更新（中文）
- [ ] docs/deployment.md 已更新（英文）
- [ ] docs/deployment.zh-CN.md 已更新（中文）
- [ ] 所有示例代码已验证可运行

### 配置
- [ ] .env.example 包含所有新的环境变量
- [ ] pyproject.toml 版本号已更新
- [ ] manage/pyproject.toml 版本号已更新
- [ ] 不再引用旧的命名约定（orion → taurus）
- [ ] 不再使用内部文件路径引用
- [ ] 所有占位符已替换为公共值

### 安全
- [ ] 依赖项已更新到最新安全版本：`poetry update`
- [ ] 没有已知的安全漏洞
- [ ] 证书生成使用安全参数
- [ ] 私钥权限已正确设置（600）
- [ ] 所有敏感数据通过环境变量管理
- [ ] SECURITY.md 已更新

### 许可和法律
- [ ] LICENSE 文件存在且正确
- [ ] 所有第三方依赖许可证已检查
- [ ] CODE_OF_CONDUCT.md 已更新
- [ ] CONTRIBUTING.md 已更新

## 发布过程（Release）

### 版本控制
- [ ] 创建发布分支：`git checkout -b release/vX.Y.Z`
- [ ] 最终提交消息格式：`Release vX.Y.Z`
- [ ] 标签创建：`git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- [ ] 标签推送：`git push origin vX.Y.Z`

### 构建产物
- [ ] Linux 二进制文件已构建（x86_64）
- [ ] Linux 二进制文件已构建（aarch64）
- [ ] macOS 二进制文件已构建（x86_64）
- [ ] macOS 二进制文件已构建（arm64）
- [ ] Docker 镜像已构建并标记：`taurus-executor:vX.Y.Z`
- [ ] Docker 镜像已推送到容器仓库
- [ ] SHA256 校验和已计算所有文件
- [ ] 校验和文件已签名

### GitHub Release
- [ ] GitHub Release 已创建
- [ ] 变更日志已添加到 Release 描述
- [ ] 二进制文件已上传
- [ ] 校验和文件已上传
- [ ] Release 已标记为正式版（非预发布）

## 发布后（Post-Release）

### 验证
- [ ] 从 GitHub Release 下载的二进制文件可运行
- [ ] Docker 镜像可拉取并运行
- [ ] 文档链接正确
- [ ] 自动更新检测到新版本

### 通信
- [ ] 已在项目聊天中宣布发布
- [ ] 已向关键用户发送通知
- [ ] 社交媒体已更新（如适用）

### 开发恢复
- [ ] 开发分支版本已更新到下一个预发布版本
- [ ] CHANGELOG 的"未发布"部分已重置
- [ ] 新功能开发的 Issue 已创建

## 回滚计划（如果需要）

### 立即操作
- [ ] GitHub Release 标记为已撤回
- [ ] Docker 镜像 `:latest` 标签指向上一个稳定版本
- [ ] 服务端的自动更新清单指向上一个版本

### 调查
- [ ] 确定问题的根本原因
- [ ] 创建问题报告
- [ ] 开发修复补丁
- [ ] 在干净环境中测试修复

### 重新发布
- [ ] 创建补丁版本（vX.Y.Z+1）
- [ ] 遵循完整的发布检查清单
- [ ] 宣布补丁版本
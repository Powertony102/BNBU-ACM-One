# 邮箱验证码与找回密码功能设计

## 1. 目标

为 `One BNBU-ACM` 增加一套可复用的邮箱验证码能力，并先落地在“找回密码”场景中，满足以下目标：

- 用户忘记密码时，可以通过“用户名 + 学校邮箱 + 邮箱验证码”完成密码重置。
- 验证码通过 `Resend` 发送邮件，邮件发送仍走 Django 官方邮件抽象层，便于本地调试与测试。
- 验证码流程具备最基础的安全控制，包括有效期、冷却时间、最大尝试次数、单次使用。

## 2. 现状分析

当前系统的认证流程特点如下：

- 登录依赖 Django 默认认证逻辑，使用 `User.username` + `password` 登录。
- 队员注册时，`username` 被强制要求为 10 位数字学号，同时同值写入 `MemberProfile.student_id`。
- 系统当前没有“找回密码”入口，也没有邮件发送能力、验证码模型、验证码页面或重置密码页面。
- 项目目前没有依赖清单文件，因此 `Resend` 接入说明需要同时写入文档，并在代码中提供“未配置 Resend 时的本地回退行为”。

## 3. 功能范围

本次实现包含：

1. 邮箱发送能力配置
   - 按 `Resend` 官方 Django 文档接入 `django-anymail[resend]`
   - 支持通过环境变量配置 API Key 和发件人
   - 在未配置 `Resend` 时保留本地开发回退方案

2. 邮箱验证码能力
   - 新增验证码数据模型
   - 支持“用途”字段，首个用途为 `password_reset`
   - 支持验证码生成、发送、校验、失效和最大尝试次数限制

3. 找回密码流程
   - 登录页增加“忘记密码”入口
   - 第一步：填写用户名和学校邮箱，请求发送验证码
   - 第二步：填写验证码和新密码，完成重置
   - 重置成功后跳回登录页

4. 测试与文档
   - 增加验证码发送与密码重置测试
   - 在 `doc/` 中补充配置说明与实现说明

本次暂不实现：

- 图形验证码
- 短信验证码
- 基于邮箱链接的一次性重置链接
- 管理端批量重置密码

## 4. 页面与流程设计

### 4.1 入口

- 在登录页新增“忘记密码？”链接
- 链接跳转到 `/password-reset/`

### 4.2 第一步：发送验证码

页面：`/password-reset/`

用户输入：

- 用户名
- 学校邮箱

服务端校验：

- 用户名必须存在
- 账号必须是激活状态
- 邮箱必须与 `User.email` 一致
- 对同一账号/邮箱/用途，发送行为需要满足冷却时间

通过后执行：

- 生成 6 位数字验证码
- 旧的未使用验证码失效
- 保存新的验证码记录
- 通过 Resend 发送邮件
- 跳转到 `/password-reset/confirm/`

### 4.3 第二步：校验验证码并重置密码

页面：`/password-reset/confirm/`

用户输入：

- 用户名
- 学校邮箱
- 6 位验证码
- 新密码
- 确认新密码

服务端校验：

- 用户名与邮箱必须匹配激活账号
- 存在未使用且未过期的验证码
- 输入验证码必须匹配
- 同一验证码的尝试次数不可超过上限
- 新密码必须通过 Django 密码强度校验

通过后执行：

- 设置新密码
- 标记验证码已使用
- 让同用途的其他待使用验证码失效
- 记录审计日志
- 提示用户密码重置成功并返回登录页

## 5. 数据模型设计

建议新增模型：`EmailVerificationCode`

核心字段：

- `user`: 关联 `User`
- `email`: 收件邮箱，冗余保存，便于校验和追踪
- `purpose`: 验证码用途，首期值为 `password_reset`
- `code`: 存储哈希后的验证码，不存明文
- `expires_at`: 过期时间
- `used_at`: 使用时间，空表示未使用
- `attempt_count`: 已尝试次数
- `created_at`: 创建时间

设计理由：

- 哈希存储可避免数据库泄露时直接暴露验证码
- `purpose` 让模型后续可复用到注册验证、邮箱换绑等场景
- `attempt_count` 可以对暴力尝试做基础限制

## 6. 后端实现方案

### 6.1 配置层

在 `settings.py` 增加：

- `RESEND_API_KEY`
- `DEFAULT_FROM_EMAIL`
- `PASSWORD_RESET_CODE_TTL_SECONDS`
- `PASSWORD_RESET_CODE_COOLDOWN_SECONDS`
- `PASSWORD_RESET_CODE_MAX_ATTEMPTS`

邮件后端策略：

- 若配置了 `RESEND_API_KEY`，使用 `anymail.backends.resend.EmailBackend`
- 若未配置，则在本地开发环境回退到 `console` backend
- 若配置了 `RESEND_API_KEY` 但未安装 `django-anymail[resend]`，启动时报错

### 6.2 服务层

新增验证码服务函数，负责：

- 生成 6 位随机数字验证码
- 创建验证码记录
- 使旧验证码失效
- 发送邮件
- 校验验证码是否正确、过期、已使用、超出尝试次数

邮件内容使用 Django 模板渲染：

- `templates/emails/password_reset_code.txt`
- `templates/emails/password_reset_code.html`

### 6.3 表单层

新增：

- `PasswordResetRequestForm`
- `PasswordResetConfirmForm`

职责：

- 完成账号与邮箱匹配校验
- 控制验证码格式、密码一致性、密码强度
- 封装可复用的错误信息

### 6.4 视图与路由

新增视图：

- `password_reset_request_view`
- `password_reset_confirm_view`

新增路由：

- `/password-reset/`
- `/password-reset/confirm/`

### 6.5 模板层

新增页面模板：

- `templates/core/password_reset_request.html`
- `templates/core/password_reset_confirm.html`

以及邮件模板：

- `templates/emails/password_reset_code.txt`
- `templates/emails/password_reset_code.html`

## 7. 安全与交互约束

- 验证码有效期默认 10 分钟
- 同一账号默认 60 秒内只能发送一次新验证码
- 同一验证码最多允许尝试 5 次
- 新验证码发出后，旧验证码立即失效
- 验证码仅可使用一次
- 仅允许通过已绑定邮箱完成密码找回

## 8. 代码落点

本次预计修改以下文件：

- `one_bnbu_acm/settings.py`
- `core/models.py`
- `core/forms.py`
- `core/views.py`
- `core/urls.py`
- `core/admin.py`
- `core/tests.py`
- `templates/core/login.html`
- `templates/core/password_reset_request.html`
- `templates/core/password_reset_confirm.html`
- `templates/emails/password_reset_code.txt`
- `templates/emails/password_reset_code.html`
- `doc/password-reset-email-verification.md`

并新增一条 migration。

## 9. Resend 接入说明

参考官方文档：

- [Send emails with Django - Resend](https://resend.com/docs/send-with-django)

建议安装：

```bash
pip install django-anymail[resend]
```

建议环境变量：

```bash
export RESEND_API_KEY="re_xxxxxxxxx"
export DEFAULT_FROM_EMAIL="no-reply@your-domain.com"
```

说明：

- `DEFAULT_FROM_EMAIL` 应使用已在 Resend 中验证过的域名
- 优先使用纯邮箱地址作为发件人，避免发件人显示名兼容性问题
- 本地未配置 `RESEND_API_KEY` 时，系统回退到控制台邮件后端，便于开发调试

## 10. 验收标准

- 登录页可进入“忘记密码”页面
- 用户可通过“用户名 + 学校邮箱”发送验证码
- 验证码邮件能够通过 Django 邮件系统发送
- 用户输入正确验证码后可成功设置新密码
- 旧密码失效，新密码可立即登录
- 错误验证码、过期验证码、超过尝试次数均会被拒绝

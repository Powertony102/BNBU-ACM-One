# One BNBU-ACM Prototype Backlog

版本：`v0.1`
日期：`2026-06-08`
状态：原型开发清单

## 1. 文档目标

本文档用于把已确认的需求拆成可执行的原型开发项，覆盖：

- 原型模块划分
- 页面清单
- 数据表清单
- 接口清单
- 开发顺序

## 2. 原型范围

本次原型以 `Django + SQLite` 为基础，优先完成：

1. 账号与权限
2. 队员个人中心
3. 活动管理
4. 签到管理
5. 二维码签到
6. `ACM Star` 展示
7. 基础统计

## 3. 模块拆分

### 3.1 认证与权限模块

目标：

- 支持登录与退出
- 支持 `Member`、`Admin`、`Super Admin` 角色区分
- 支持页面级和操作级权限控制

### 3.2 队员模块

目标：

- 队员查看个人首页
- 队员查看活动与签到历史
- 队员维护个人资料

### 3.3 活动管理模块

目标：

- 管理员创建、编辑、发布、关闭、作废活动
- 管理员查看活动详情与签到情况

### 3.4 签到模块

目标：

- 队员页面签到
- 队员扫码签到
- 管理员补签与撤销签到

### 3.5 二维码模块

目标：

- 为活动生成唯一签到链接
- 将签到链接渲染为二维码
- 支持未登录扫码后跳转登录并返回签到页

### 3.6 Star 与统计模块

目标：

- 首页展示 `ACM Star`
- 统计近期活跃情况
- 提供管理端基础统计面板

### 3.7 系统配置与审计模块

目标：

- 支持 `Super Admin` 配置系统参数
- 记录关键操作日志

## 4. 页面清单

### 4.1 公共页面

#### P-01 登录页

角色：

- `Member`
- `Admin`
- `Super Admin`

核心内容：

- 用户名输入
- 密码输入
- 登录按钮
- 登录失败反馈

备注：

- 支持登录后按角色跳转
- 支持带 `next` 参数回跳，例如扫码后登录

#### P-02 无权限页

角色：

- 全角色

核心内容：

- 权限不足提示
- 返回首页或登录页入口

### 4.2 队员侧页面

#### P-10 队员首页

角色：

- `Member`

核心内容：

- `ACM Star` 状态卡片
- 最近参加活动
- 近期可签到活动
- 个人累计签到次数

#### P-11 活动列表页

角色：

- `Member`

核心内容：

- 活动列表
- 活动状态
- 活动时间地点
- 进入详情按钮

#### P-12 活动详情页

角色：

- `Member`

核心内容：

- 活动基本信息
- 当前签到状态
- 签到按钮
- 签到结果反馈

#### P-13 签到历史页

角色：

- `Member`

核心内容：

- 历史签到记录列表
- 签到时间
- 签到方式
- 对应活动

#### P-14 个人资料页

角色：

- `Member`

核心内容：

- 个人资料展示
- 可编辑字段表单
- 保存资料按钮

#### P-15 扫码签到页

角色：

- `Member`

核心内容：

- 活动信息确认
- 当前用户确认
- 签到按钮或自动签到反馈
- 成功/失败提示

备注：

- 如果用户未登录，应先跳转登录页，再回到该页

### 4.3 管理端页面

#### P-20 管理首页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 近期活动数
- 今日签到数
- Star 点亮人数
- 活跃队员数

#### P-21 活动列表管理页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 活动列表
- 状态筛选
- 新建活动按钮
- 编辑入口
- 查看签到入口

#### P-22 活动创建/编辑页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 活动表单
- 时间设置
- 签到时间窗口设置
- 发布/保存草稿按钮

#### P-23 活动详情管理页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 活动信息
- 签到统计
- 二维码生成区
- 活动状态操作按钮

#### P-24 活动签到名单页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 已签到名单
- 未签到名单
- 补签按钮
- 撤销签到按钮

#### P-25 队员列表管理页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 队员列表
- 搜索
- 新增队员按钮
- 编辑按钮
- 停用/启用按钮

#### P-26 队员详情管理页

角色：

- `Admin`
- `Super Admin`

核心内容：

- 队员资料
- 签到历史
- 密码重置入口
- 账号状态控制

#### P-27 管理员管理页

角色：

- `Super Admin`

核心内容：

- 管理员列表
- 新增管理员
- 调整管理员级别
- 启用/停用管理员

#### P-28 系统设置页

角色：

- `Super Admin`

核心内容：

- `ACM Star` 近期窗口天数
- 二维码有效策略
- 其他系统参数

#### P-29 审计日志页

角色：

- `Super Admin`

核心内容：

- 操作时间
- 操作人
- 操作类型
- 目标对象

## 5. 数据表清单

### 5.1 `users`

用途：

- 系统登录账号

关键字段：

- `id`
- `username`
- `password_hash`
- `role`
- `is_active`
- `last_login_at`
- `created_at`
- `updated_at`

说明：

- 建议基于 Django `AbstractUser` 或用户扩展实现

### 5.2 `member_profiles`

用途：

- 保存队员业务资料

关键字段：

- `id`
- `user_id`
- `real_name`
- `student_id`
- `email`
- `phone`
- `major`
- `class_name`
- `status`
- `created_at`
- `updated_at`

### 5.3 `admin_profiles`

用途：

- 保存管理员资料与级别

关键字段：

- `id`
- `user_id`
- `display_name`
- `admin_level`
- `status`
- `created_at`
- `updated_at`

### 5.4 `events`

用途：

- 保存活动信息

关键字段：

- `id`
- `title`
- `event_type`
- `description`
- `location`
- `start_time`
- `end_time`
- `checkin_start_time`
- `checkin_end_time`
- `status`
- `created_by_id`
- `published_at`
- `created_at`
- `updated_at`

### 5.5 `event_qr_codes`

用途：

- 保存活动签到二维码入口信息

关键字段：

- `id`
- `event_id`
- `token`
- `url`
- `is_active`
- `expires_at`
- `created_by_id`
- `created_at`

说明：

- 原型阶段建议一个活动只保留一个当前有效二维码

### 5.6 `checkin_records`

用途：

- 保存签到记录

关键字段：

- `id`
- `member_id`
- `event_id`
- `checkin_time`
- `checkin_method`
- `status`
- `source_qr_code_id`
- `remark`
- `created_by_id`
- `created_at`
- `updated_at`

约束建议：

- `member_id + event_id + status=valid` 不允许重复有效记录

### 5.7 `system_settings`

用途：

- 保存系统参数

关键字段：

- `id`
- `key`
- `value`
- `updated_by_id`
- `updated_at`

建议初始参数：

- `star_recent_window_days`
- `qr_code_expire_minutes`

### 5.8 `audit_logs`

用途：

- 保存关键操作日志

关键字段：

- `id`
- `operator_id`
- `action`
- `target_type`
- `target_id`
- `detail`
- `created_at`

## 6. 接口清单

原型阶段建议采用 Django 服务端渲染为主，但仍建议按清晰的 URL / action 设计接口。

### 6.1 认证接口

#### `GET /login/`

- 显示登录页

#### `POST /login/`

- 处理登录请求

#### `POST /logout/`

- 处理退出请求

### 6.2 队员侧接口

#### `GET /member/dashboard/`

- 队员首页

#### `GET /member/events/`

- 队员活动列表

#### `GET /member/events/{event_id}/`

- 队员活动详情

#### `POST /member/events/{event_id}/check-in/`

- 页面签到

#### `GET /member/check-ins/`

- 队员签到历史

#### `GET /member/profile/`

- 查看个人资料

#### `POST /member/profile/`

- 更新个人资料

### 6.3 二维码签到接口

#### `GET /qr/{token}/`

- 扫码签到入口
- 若未登录则跳转登录并带回跳地址

#### `POST /qr/{token}/check-in/`

- 对二维码对应活动执行签到

### 6.4 活动管理接口

#### `GET /admin/events/`

- 活动列表管理页

#### `GET /admin/events/create/`

- 活动创建页

#### `POST /admin/events/create/`

- 创建活动

#### `GET /admin/events/{event_id}/edit/`

- 活动编辑页

#### `POST /admin/events/{event_id}/edit/`

- 更新活动

#### `POST /admin/events/{event_id}/publish/`

- 发布活动

#### `POST /admin/events/{event_id}/close-check-in/`

- 关闭签到

#### `POST /admin/events/{event_id}/cancel/`

- 作废活动

#### `GET /admin/events/{event_id}/`

- 活动详情管理页

### 6.5 二维码管理接口

#### `POST /admin/events/{event_id}/qr-code/generate/`

- 为活动生成二维码

#### `GET /admin/events/{event_id}/qr-code/`

- 查看当前二维码信息

### 6.6 签到管理接口

#### `GET /admin/events/{event_id}/check-ins/`

- 查看活动签到名单

#### `POST /admin/events/{event_id}/check-ins/manual/`

- 手动补签

#### `POST /admin/check-ins/{checkin_id}/revoke/`

- 撤销签到

### 6.7 队员管理接口

#### `GET /admin/members/`

- 队员列表管理页

#### `GET /admin/members/create/`

- 新增队员页

#### `POST /admin/members/create/`

- 新增队员

#### `GET /admin/members/{member_id}/`

- 队员详情页

#### `POST /admin/members/{member_id}/edit/`

- 更新队员资料

#### `POST /admin/members/{member_id}/toggle-status/`

- 启用或停用队员

#### `POST /admin/members/{member_id}/reset-password/`

- 重置队员密码

### 6.8 管理员与系统配置接口

#### `GET /super-admin/admins/`

- 管理员管理页

#### `POST /super-admin/admins/create/`

- 新增管理员

#### `POST /super-admin/admins/{admin_id}/edit/`

- 更新管理员资料与级别

#### `POST /super-admin/admins/{admin_id}/toggle-status/`

- 启用或停用管理员

#### `GET /super-admin/settings/`

- 系统设置页

#### `POST /super-admin/settings/`

- 更新系统参数

#### `GET /super-admin/audit-logs/`

- 审计日志页

## 7. 原型开发顺序

### 7.1 第一阶段：项目骨架

目标：

- 建立 Django 项目
- 配置 SQLite
- 建立基础模板、路由和权限骨架

交付物：

- 可运行项目
- 登录页
- 角色跳转逻辑

### 7.2 第二阶段：用户与权限

目标：

- 完成 `users`、`member_profiles`、`admin_profiles`
- 完成角色权限校验

交付物：

- 队员/管理员/超级管理员账号模型
- 页面权限保护

### 7.3 第三阶段：活动管理

目标：

- 完成 `events`
- 支持活动创建、编辑、发布、关闭

交付物：

- 管理端活动列表
- 活动创建与编辑页面

### 7.4 第四阶段：队员侧基础页面

目标：

- 完成队员首页、活动列表、活动详情、个人资料、签到历史

交付物：

- 队员端核心浏览能力
- 资料修改能力

### 7.5 第五阶段：签到能力

目标：

- 完成 `checkin_records`
- 支持页面签到、手动补签、撤销签到

交付物：

- 队员签到功能
- 管理员签到管理功能

### 7.6 第六阶段：二维码签到

目标：

- 完成 `event_qr_codes`
- 支持二维码生成和扫码签到流程

交付物：

- 活动二维码
- 未登录回跳
- 扫码签到成功/失败反馈

### 7.7 第七阶段：Star 与统计

目标：

- 实现 `ACM Star` 计算
- 实现首页与管理端基础统计

交付物：

- 队员首页 Star 状态
- 管理端统计面板

### 7.8 第八阶段：系统设置与审计

目标：

- 完成 `system_settings` 和 `audit_logs`
- 支持超级管理员配置参数

交付物：

- 系统设置页
- 审计日志页

## 8. MVP 优先级

### 8.1 P0 必做

- 登录/退出
- 角色权限控制
- 队员首页
- 活动创建与管理
- 队员页面签到
- 二维码生成
- 扫码登录后签到
- `ACM Star` 展示

### 8.2 P1 应做

- 队员资料修改
- 管理员补签与撤销签到
- 队员签到历史
- 基础统计面板

### 8.3 P2 可延后

- 管理员完整分级细化
- 审计日志细节增强
- 二维码失效策略增强
- 批量导入导出

## 9. 原型验收标准

### 9.1 队员侧验收

- 队员可以成功登录
- 队员可以查看自己的 `ACM Star`
- 队员可以查看活动并完成签到
- 队员可以扫码后登录并完成签到
- 队员可以修改个人资料中的允许字段

### 9.2 管理端验收

- 管理员可以创建、发布、编辑活动
- 管理员可以为活动生成二维码
- 管理员可以查看签到名单
- 管理员可以补签和撤销签到
- 管理员可以管理队员资料

### 9.3 超级管理员验收

- 超级管理员可以管理管理员账号
- 超级管理员可以修改 `ACM Star` 参数
- 超级管理员可以查看审计日志

## 10. 下一步建议

这份 backlog 完成后，下一步最适合直接进入：

1. Django 项目初始化
2. 数据模型落表
3. 登录与角色权限骨架实现

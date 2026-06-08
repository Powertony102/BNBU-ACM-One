# One BNBU-ACM Actor / Use Case / Class Model

版本：`v0.2`
日期：`2026-06-09`
状态：原型建模草稿

## 1. 建模目标

本文档用于在正式开发前明确：

- actor 有哪些
- 每个 actor 需要什么 use case
- 核心 classes 应如何划分
- 权限边界和关系如何设计

## 2. Actor 模型

### 2.1 Member

定位：系统的普通使用者，即 ACM 队员。

核心诉求：

- 看自己最近是否活跃
- 看活动
- 申请自己要组织的活动
- 快速签到
- 修改自己的个人资料
- 修改自己的登录密码

Use cases：

- 登录系统
- 退出系统
- 查看个人首页
- 查看 `ACM Star`
- 查看活动列表
- 查看活动详情
- 提交活动申请
- 管理自己获批活动的签到
- 查看个人签到历史
- 修改个人资料
- 通过邮箱验证码修改自己的登录密码
- 通过页面签到
- 通过二维码签到

### 2.2 Admin

定位：负责日常运营的管理员。

核心诉求：

- 管活动
- 审活动申请
- 管签到
- 管队员
- 看统计
- 维护自己的后台登录密码

Use cases：

- 登录后台
- 退出系统
- 查看管理首页
- 创建活动
- 编辑活动
- 删除活动（软删除为已作废）
- 为活动指定 Member 作为签到管理员
- 审核成员活动申请
- 发布活动
- 关闭签到
- 作废活动
- 生成签到二维码
- 查看活动签到名单
- 手动补签
- 撤销签到
- 新增队员
- 编辑队员资料
- 停用或启用队员账号
- 重置队员密码
- 查看队员参与历史
- 查看统计信息
- 通过邮箱验证码修改自己的登录密码

### 2.3 Super Admin

定位：拥有系统级管理能力的高级管理员。

核心诉求：

- 管理管理员
- 管理系统参数
- 控制权限边界
- 维护自己的系统登录密码

Use cases：

- 创建管理员账号
- 编辑管理员资料
- 分配管理员级别
- 停用或启用管理员账号
- 配置 `ACM Star` 近期窗口
- 配置二维码策略
- 查看完整审计日志
- 通过邮箱验证码修改自己的登录密码

### 2.4 System

定位：后台自动规则执行者，不是人类用户。

核心诉求：

- 确保规则正确执行
- 确保数据一致性

Use cases：

- 用户身份校验
- 角色权限校验
- 活动状态判定
- 签到窗口判定
- 重复签到校验
- 二维码 token 校验
- 登录后重定向回签到入口
- 计算 `ACM Star`
- 刷新统计数据
- 记录审计日志

## 3. Actor 关系

建议关系如下：

- `Super Admin` 是 `Admin` 的高权限扩展
- `Member` 与 `Admin` 在系统中应使用不同角色标识
- `System` 不直接操作界面，但参与几乎所有关键 use case

## 4. Use Case 分组

### 4.1 认证与会话

- 登录
- 退出
- 已登录状态下修改密码
- 登录后按角色跳转
- 未登录扫码后跳转登录页
- 登录成功后回到原签到入口

### 4.2 队员侧功能

- 首页展示
- Star 状态展示
- 活动查看
- 活动申请
- 自己获批活动的签到管理
- 自助签到
- 签到历史查询
- 资料修改

### 4.3 管理端功能

- 活动生命周期管理
- 活动编辑与删除
- 活动申请审核
- 签到二维码生成
- 签到记录查看与修正
- 队员账号管理
- 管理端统计

### 4.4 系统级功能

- 参数配置
- 管理员分级
- 审计日志
- 状态计算

## 5. 初步 Class 划分

### 5.1 User

职责：

- 保存登录账号信息
- 标识角色
- 作为权限校验入口

关键属性：

- `username`
- `password_hash`
- `role`
- `is_active`

### 5.2 MemberProfile

职责：

- 保存队员业务信息

关键属性：

- `real_name`
- `student_id`
- `email`
- `phone`
- `major`
- `class_name`

### 5.3 AdminProfile

职责：

- 保存管理员业务信息和分级信息

关键属性：

- `display_name`
- `admin_level`
- `status`

### 5.4 Event

职责：

- 描述活动本身
- 维护活动生命周期
- 在成员申请场景下记录审核状态与事件级签到管理授权

关键属性：

- `title`
- `event_type`
- `description`
- `location`
- `start_time`
- `end_time`
- `checkin_start_time`
- `checkin_end_time`
- `status`
- `applicant`
- `checkin_manager`
- `review_status`
- `reviewed_by`
- `review_note`
- `reviewed_at`

### 5.5 EventQRCode

职责：

- 持有活动签到二维码的业务信息
- 绑定活动签到入口

关键属性：

- `token`
- `url`
- `is_active`
- `expires_at`

### 5.6 CheckInRecord

职责：

- 保存队员对活动的签到结果

关键属性：

- `member`
- `event`
- `checkin_time`
- `checkin_method`
- `status`
- `source_qr_code`

### 5.7 ACMStarStatus

职责：

- 表示或缓存队员当前星标状态

关键属性：

- `member`
- `is_lit`
- `last_participation_time`
- `recent_window_days`

### 5.8 SystemSetting

职责：

- 保存系统参数

关键属性：

- `key`
- `value`

### 5.9 AuditLog

职责：

- 审计系统关键操作

关键属性：

- `operator`
- `action`
- `target_type`
- `target_id`
- `detail`

## 6. 关键关系

### 6.1 用户与资料

- 一个 `User` 对应一个 `MemberProfile` 或一个 `AdminProfile`
- `Super Admin` 建议仍基于 `User.role` 与 `AdminProfile.admin_level` 表示

### 6.2 活动与签到

- 一个 `Event` 对应多个 `CheckInRecord`
- 一个 `MemberProfile` 对应多个 `CheckInRecord`

### 6.3 活动与二维码

- 一个 `Event` 可对应一个或多个 `EventQRCode`
- 原型阶段建议先支持“一场活动一个当前有效二维码”

### 6.4 队员与 Star

- 一个 `MemberProfile` 对应一个 `ACMStarStatus`
- 也可以在运行时根据 `CheckInRecord` 计算

## 7. 原型阶段推荐简化

为了尽快启动 Django 原型，建议先采用以下简化策略：

1. `User` 使用 Django 自带认证体系扩展角色字段。
2. `MemberProfile` 与 `AdminProfile` 分开建模。
3. `EventQRCode` 先只存储一个 `token` 和活动绑定关系。
4. `ACMStarStatus` 第一版可按需计算，不一定立即落表。
5. 权限分级先实现两层：`Admin` 和 `Super Admin`。

## 8. 后续可直接转为开发项的模块

1. 认证与权限模块
2. 队员资料模块
3. 活动管理模块
4. 签到与二维码模块
5. Star 状态模块
6. 统计与日志模块

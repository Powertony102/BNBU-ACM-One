#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# ACM System - 服务器部署脚本
# 用法: sudo bash start_at_server.sh
# ============================================================

# ----- 配置区（按需修改）-----
DOMAIN="${DOMAIN:-your-domain.com}"                  # 你的域名
PROJECT_DIR="${PROJECT_DIR:-/www/wwwroot/${DOMAIN}}"  # 项目根目录
BACKEND_DIR="${PROJECT_DIR}"                          # Django 项目目录（manage.py 所在）
VENV_DIR="${BACKEND_DIR}/venv"                        # 虚拟环境路径
SERVICE_NAME="acm-django"                             # systemd 服务名
GUNICORN_PORT=8001                                    # Gunicorn 监听端口
GUNICORN_WORKERS=3                                    # Gunicorn 工作进程数
WWW_USER="www"                                        # 宝塔默认用户
WWW_GROUP="www"                                       # 宝塔默认用户组

# Django 生产环境变量
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')}"
export DJANGO_DEBUG="False"
export DJANGO_ALLOWED_HOSTS="${DOMAIN},www.${DOMAIN},127.0.0.1,localhost"
export DJANGO_CSRF_TRUSTED_ORIGINS="https://${DOMAIN},https://www.${DOMAIN}"

# Resend 邮件配置（可选）
export RESEND_API_KEY="${RESEND_API_KEY:-}"
export DEFAULT_FROM_EMAIL="${DEFAULT_FROM_EMAIL:-no-reply@${DOMAIN}}"

# ----- 检查 root 权限 -----
if [[ $EUID -ne 0 ]]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

echo "=========================================="
echo "  ACM System 部署脚本"
echo "=========================================="
echo "域名:       ${DOMAIN}"
echo "项目目录:   ${PROJECT_DIR}"
echo "服务名:     ${SERVICE_NAME}"
echo "=========================================="

# ----- 1. 检查项目目录 -----
echo ""
echo "[1/7] 检查项目目录..."
if [[ ! -f "${BACKEND_DIR}/manage.py" ]]; then
    echo "错误: ${BACKEND_DIR}/manage.py 不存在"
    echo "请先将项目文件上传到 ${PROJECT_DIR}"
    exit 1
fi

# ----- 2. 创建虚拟环境并安装依赖 -----
echo ""
echo "[2/7] 创建虚拟环境并安装依赖..."
cd "${BACKEND_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "依赖安装完成"

# ----- 3. 数据库迁移 -----
echo ""
echo "[3/7] 执行数据库迁移..."
python manage.py migrate --noinput

# ----- 4. 收集静态文件 -----
echo ""
echo "[4/7] 收集静态文件..."
python manage.py collectstatic --noinput

# ----- 5. 引导演示数据（首次部署）-----
echo ""
echo "[5/7] 初始化演示数据..."
python manage.py bootstrap_demo || true

# ----- 6. 创建 systemd 服务 -----
echo ""
echo "[6/7] 创建 systemd 服务..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=ACM System Django Gunicorn Service
After=network.target

[Service]
User=${WWW_USER}
Group=${WWW_GROUP}
WorkingDirectory=${BACKEND_DIR}
Environment="PATH=${VENV_DIR}/bin"
Environment="DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}"
Environment="DJANGO_DEBUG=False"
Environment="DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}"
Environment="DJANGO_CSRF_TRUSTED_ORIGINS=${DJANGO_CSRF_TRUSTED_ORIGINS}"
Environment="RESEND_API_KEY=${RESEND_API_KEY}"
Environment="DEFAULT_FROM_EMAIL=${DEFAULT_FROM_EMAIL}"
ExecStart=${VENV_DIR}/bin/gunicorn one_bnbu_acm.wsgi:application \\
    --workers ${GUNICORN_WORKERS} \\
    --bind 127.0.0.1:${GUNICORN_PORT} \\
    --access-logfile /var/log/${SERVICE_NAME}-access.log \\
    --error-logfile /var/log/${SERVICE_NAME}-error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# ----- 7. 检查服务状态 -----
echo ""
echo "[7/7] 检查服务状态..."
sleep 2

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo ""
    echo "=========================================="
    echo "  部署成功！"
    echo "=========================================="
    echo ""
    echo "  Django 服务已启动: http://127.0.0.1:${GUNICORN_PORT}"
    echo ""
    echo "  接下来请在宝塔面板操作："
    echo "  1. 网站 → 添加站点"
    echo "     域名: ${DOMAIN} www.${DOMAIN}"
    echo "     根目录: ${PROJECT_DIR}"
    echo "     PHP: 纯静态"
    echo ""
    echo "  2. 网站设置 → 反向代理 → 添加反向代理"
    echo "     代理名称: acm"
    echo "     目标URL: http://127.0.0.1:${GUNICORN_PORT}"
    echo ""
    echo "  3. 网站设置 → SSL → Let's Encrypt → 申请证书"
    echo "     开启: 强制 HTTPS"
    echo ""
    echo "  超级管理员: superadmin / ACM123456"
    echo "  普通会员:   member01 / ACM123456"
    echo ""
    echo "  查看服务状态: systemctl status ${SERVICE_NAME}"
    echo "  查看错误日志: journalctl -u ${SERVICE_NAME} -n 50"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "  服务启动失败！"
    echo "=========================================="
    echo ""
    echo "  请查看错误日志:"
    echo "  journalctl -u ${SERVICE_NAME} -n 50"
    echo ""
    echo "  或手动测试 Gunicorn:"
    echo "  cd ${BACKEND_DIR}"
    echo "  source venv/bin/activate"
    echo "  gunicorn one_bnbu_acm.wsgi:application --bind 127.0.0.1:${GUNICORN_PORT}"
    echo "=========================================="
    exit 1
fi

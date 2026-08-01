import os
import sys
import time
import json
import logging
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import database as db_module
from common.config import load_config, APP_NAME, APP_VERSION, UPLOAD_DIR
from common.utils import hash_password, verify_password

logger = logging.getLogger(__name__)

def create_web_app(config):
    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web', 'web_templates')
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web', 'web_static')
    
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.secret_key = 'yunyinbao_web_secret_2026'
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    
    db_module.init_db()
    
    from server.printer_manager import PrinterManager
    from server.user_manager import UserManager
    from server.device_manager import DeviceManager
    from server.task_scheduler import TaskScheduler
    from server.print_engine import PrintEngine
    
    printer_mgr = PrinterManager(db_module)
    user_mgr = UserManager(db_module)
    device_mgr = DeviceManager(db_module, config)
    print_engine = PrintEngine()
    task_scheduler = TaskScheduler(db_module, printer_mgr, print_engine)
    task_scheduler.start()
    
    app.extensions['printer_mgr'] = printer_mgr
    app.extensions['user_mgr'] = user_mgr
    app.extensions['device_mgr'] = device_mgr
    app.extensions['task_scheduler'] = task_scheduler
    
    @app.template_filter('datetime_format')
    def datetime_format(value):
        if not value:
            return '-'
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
        except:
            return str(value)

    def login_required(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session or session.get('role') != 'admin':
                return redirect(url_for('web_login'))
            return f(*args, **kwargs)
        return wrapper

    @app.route('/')
    def index():
        if 'user_id' in session and session.get('role') == 'admin':
            return redirect(url_for('web_dashboard'))
        return redirect(url_for('web_login'))

    @app.route('/login', methods=['GET', 'POST'])
    def web_login():
        if request.method == 'POST':
            data = request.form
            username = data.get('username', '')
            password = data.get('password', '')
            
            user, msg = user_mgr.authenticate(username, password)
            if user and user.get('role') == 'admin':
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['real_name'] = user.get('real_name', '')
                return redirect(url_for('web_dashboard'))
            
            error = msg or '用户名或密码错误'
            return render_template('login.html', error=error, app_name=APP_NAME, version=APP_VERSION)
        
        return render_template('login.html', app_name=APP_NAME, version=APP_VERSION)

    @app.route('/logout')
    def web_logout():
        session.clear()
        return redirect(url_for('web_login'))

    @app.route('/dashboard')
    @login_required
    def web_dashboard():
        info = task_scheduler.get_queue_info()
        
        conn = db_module.get_db()
        today = time.strftime("%Y-%m-%d")
        today_start = time.mktime(time.strptime(today, "%Y-%m-%d"))
        
        today_stats = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(pages*copies),0) as pages FROM print_tasks WHERE created_at >= ? AND status = 'completed'",
            (today_start,)
        ).fetchone()
        
        total_stats = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(pages*copies),0) as pages FROM print_tasks WHERE status = 'completed'"
        ).fetchone()
        
        user_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE role = 'user'").fetchone()['c']
        printer_count = conn.execute("SELECT COUNT(*) as c FROM printers WHERE is_shared = 1").fetchone()['c']
        device_count = len(device_mgr.get_online_devices())
        
        recent_tasks = conn.execute(
            "SELECT * FROM print_tasks ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        recent_tasks = [dict(t) for t in recent_tasks]
        
        conn.close()
        
        return render_template('dashboard.html',
                             app_name=APP_NAME,
                             version=APP_VERSION,
                             username=session.get('real_name') or session.get('username'),
                             queue_info=info,
                             today_count=today_stats['count'],
                             today_pages=today_stats['pages'],
                             total_count=total_stats['count'],
                             total_pages=total_stats['pages'],
                             user_count=user_count,
                             printer_count=printer_count,
                             device_count=device_count,
                             recent_tasks=recent_tasks,
                             page='dashboard')

    @app.route('/printers')
    @login_required
    def web_printers():
        printers = printer_mgr.get_all_printers()
        return render_template('printers.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             printers=printers,
                             page='printers')

    @app.route('/printers/add', methods=['POST'])
    @login_required
    def add_printer():
        data = request.form
        name = data.get('name', '')
        conn_type = data.get('connection_type', 'USB')
        is_shared = 1 if data.get('is_shared') == 'on' else 0
        is_default = 1 if data.get('is_default') == 'on' else 0
        paper_size = data.get('paper_size', 'A4')
        color_mode = data.get('color_mode', 'black')
        
        pid = printer_mgr.add_printer(
            name=name,
            connection_type=conn_type,
            is_shared=is_shared,
            is_default=is_default,
            paper_size=paper_size,
            color_mode=color_mode
        )
        return redirect(url_for('web_printers'))

    @app.route('/printers/<int:pid>/delete')
    @login_required
    def delete_printer(pid):
        printer_mgr.delete_printer(pid)
        return redirect(url_for('web_printers'))

    @app.route('/printers/<int:pid>/toggle_share')
    @login_required
    def toggle_printer_share(pid):
        p = printer_mgr.get_printer(pid)
        if p:
            printer_mgr.update_printer(pid, is_shared=0 if p['is_shared'] else 1)
        return redirect(url_for('web_printers'))

    @app.route('/printers/<int:pid>/set_default')
    @login_required
    def set_printer_default(pid):
        printer_mgr.update_printer(pid, is_default=1)
        return redirect(url_for('web_printers'))

    @app.route('/tasks')
    @login_required
    def web_tasks():
        status = request.args.get('status', '')
        tasks = task_scheduler.get_all_tasks(status if status else None, limit=200)
        return render_template('tasks.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             tasks=tasks,
                             current_status=status,
                             page='tasks')

    @app.route('/tasks/<task_id>/cancel')
    @login_required
    def cancel_task(task_id):
        task_scheduler.cancel_task(task_id)
        return redirect(url_for('web_tasks'))

    @app.route('/tasks/<task_id>/retry')
    @login_required
    def retry_task(task_id):
        task_scheduler.retry_task(task_id)
        return redirect(url_for('web_tasks'))

    @app.route('/tasks/export')
    @login_required
    def export_tasks():
        import csv
        import io
        
        tasks = task_scheduler.get_all_tasks(limit=10000)
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["任务ID", "用户", "设备", "打印机", "文件名", "页数", "份数", "状态", "提交时间", "完成时间"])
        
        status_text = {"pending": "等待中", "printing": "打印中", "completed": "已完成", "failed": "失败", "cancelled": "已取消", "expired": "已过期"}
        
        for t in tasks:
            writer.writerow([
                t['task_id'],
                t.get('username', ''),
                t.get('device_name', ''),
                t.get('printer_name', ''),
                t.get('file_name', ''),
                t.get('pages', 0),
                t.get('copies', 1),
                status_text.get(t.get('status', ''), t.get('status', '')),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t['created_at'])) if t.get('created_at') else "",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t['completed_at'])) if t.get('completed_at') else ""
            ])
        
        output.seek(0)
        from flask import Response
        return Response(
            output.getvalue().encode('utf-8-sig'),
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=print_tasks_{time.strftime('%Y%m%d')}.csv"}
        )

    @app.route('/users')
    @login_required
    def web_users():
        users = user_mgr.get_all_users()
        return render_template('users.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             users=users,
                             page='users')

    @app.route('/users/add', methods=['POST'])
    @login_required
    def add_user():
        data = request.form
        username = data.get('username', '')
        password = data.get('password', '')
        real_name = data.get('real_name', '')
        department = data.get('department', '')
        role = data.get('role', 'user')
        daily_limit = int(data.get('daily_limit', 0) or 0)
        
        success, msg = user_mgr.register_user(username, password, real_name, department, role, daily_limit)
        return redirect(url_for('web_users'))

    @app.route('/users/<int:uid>/delete')
    @login_required
    def delete_user(uid):
        user_mgr.delete_user(uid)
        return redirect(url_for('web_users'))

    @app.route('/users/<int:uid>/reset_pwd')
    @login_required
    def reset_user_pwd(uid):
        user_mgr.update_user(uid, password='123456')
        return redirect(url_for('web_users'))

    @app.route('/users/<int:uid>/toggle_status')
    @login_required
    def toggle_user_status(uid):
        user = user_mgr.get_user(uid)
        if user:
            new_status = 'disabled' if user['status'] == 'active' else 'active'
            user_mgr.update_user(uid, status=new_status)
        return redirect(url_for('web_users'))

    @app.route('/devices')
    @login_required
    def web_devices():
        devices = device_mgr.get_all_devices()
        return render_template('devices.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             devices=devices,
                             page='devices')

    @app.route('/devices/<device_id>/block')
    @login_required
    def block_device(device_id):
        device_mgr.block_device(device_id)
        return redirect(url_for('web_devices'))

    @app.route('/devices/<device_id>/unblock')
    @login_required
    def unblock_device(device_id):
        device_mgr.unblock_device(device_id)
        return redirect(url_for('web_devices'))

    @app.route('/stats')
    @login_required
    def web_stats():
        conn = db_module.get_db()
        
        daily_data = []
        for i in range(6, -1, -1):
            day = time.localtime(time.time() - i * 86400)
            day_str = time.strftime("%Y-%m-%d", day)
            day_start = time.mktime(time.strptime(day_str, "%Y-%m-%d"))
            day_end = day_start + 86400
            
            stats = conn.execute(
                "SELECT COUNT(*) as count, COALESCE(SUM(pages*copies),0) as pages FROM print_tasks WHERE created_at >= ? AND created_at < ? AND status = 'completed'",
                (day_start, day_end)
            ).fetchone()
            daily_data.append({
                'date': day_str[5:],
                'count': stats['count'],
                'pages': stats['pages']
            })
        
        dept_stats = conn.execute("""
            SELECT COALESCE(NULLIF(department,''), '未分配') as dept,
                   COUNT(*) as count,
                   COALESCE(SUM(pages*copies),0) as pages
            FROM print_tasks t
            LEFT JOIN users u ON t.user_id = u.id
            WHERE t.status = 'completed'
            GROUP BY department
            ORDER BY pages DESC
            LIMIT 10
        """).fetchall()
        dept_data = [dict(d) for d in dept_stats]
        
        user_stats = conn.execute("""
            SELECT u.username, COALESCE(u.real_name, u.username) as name,
                   COUNT(*) as count,
                   COALESCE(SUM(pages*copies),0) as pages
            FROM print_tasks t
            LEFT JOIN users u ON t.user_id = u.id
            WHERE t.status = 'completed'
            GROUP BY t.user_id
            ORDER BY pages DESC
            LIMIT 10
        """).fetchall()
        user_data = [dict(u) for u in user_stats]
        
        conn.close()
        
        return render_template('stats.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             daily_data=daily_data,
                             dept_data=dept_data,
                             user_data=user_data,
                             page='stats')

    @app.route('/settings')
    @login_required
    def web_settings():
        return render_template('settings.html',
                             app_name=APP_NAME,
                             username=session.get('real_name') or session.get('username'),
                             config=config,
                             page='settings')

    @app.route('/settings/save', methods=['POST'])
    @login_required
    def save_settings():
        data = request.form
        
        if 'server.port' in data:
            config['server']['port'] = int(data.get('server.port', 8989))
        if 'server.web_port' in data:
            config['server']['web_port'] = int(data.get('server.web_port', 8990))
        if 'server.access_code' in data:
            config['server']['access_code'] = data.get('server.access_code', '')
        
        if 'security.encrypt_transfer' in data:
            config['security']['encrypt_transfer'] = data.get('security.encrypt_transfer') == 'on'
        if 'security.use_whitelist' in data:
            config['security']['use_whitelist'] = data.get('security.use_whitelist') == 'on'
        if 'security.whitelist' in data:
            wl = data.get('security.whitelist', '')
            config['security']['whitelist'] = [ip.strip() for ip in wl.split(',') if ip.strip()]
        
        from common.config import save_config
        save_config(config)
        
        return redirect(url_for('web_settings'))

    return app

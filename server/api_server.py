import os
import sys
import time
import json
import logging
import threading
from flask import Flask, request, jsonify, send_file, render_template_string, redirect, url_for, session, render_template, Response
from werkzeug.utils import secure_filename
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import database as db_module
from common.config import UPLOAD_DIR, APP_NAME, APP_VERSION, load_config
from common.utils import gen_task_id, format_size
from server.printer_manager import PrinterManager
from server.print_engine import PrintEngine
from server.task_scheduler import TaskScheduler
from server.user_manager import UserManager
from server.device_manager import DeviceManager
from server.env_check import EnvChecker

logger = logging.getLogger(__name__)

def _resource_path(rel_path):
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)

def create_app(config, existing_managers=None):
    template_dir = _resource_path(os.path.join('web', 'web_templates'))
    static_dir = _resource_path(os.path.join('web', 'web_static'))
    flask_kwargs = {'template_folder': template_dir}
    if os.path.isdir(static_dir):
        flask_kwargs['static_folder'] = static_dir
    app = Flask(__name__, **flask_kwargs)
    
    app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
    app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
    app.secret_key = 'yunyinbao_secret_2026'
    
    db_module.init_db()
    
    if existing_managers:
        printer_mgr = existing_managers.get('printer_mgr') or PrinterManager(db_module)
        print_engine = existing_managers.get('print_engine') or PrintEngine()
        task_scheduler = existing_managers.get('task_scheduler') or TaskScheduler(db_module, printer_mgr, print_engine)
        user_mgr = existing_managers.get('user_mgr') or UserManager(db_module)
        device_mgr = existing_managers.get('device_mgr') or DeviceManager(db_module, config)
        env_checker = existing_managers.get('env_checker') or EnvChecker()
    else:
        printer_mgr = PrinterManager(db_module)
        print_engine = PrintEngine()
        task_scheduler = TaskScheduler(db_module, printer_mgr, print_engine)
        user_mgr = UserManager(db_module)
        device_mgr = DeviceManager(db_module, config)
        env_checker = EnvChecker()
    
    task_scheduler.start()
    
    app.extensions['printer_mgr'] = printer_mgr
    app.extensions['print_engine'] = print_engine
    app.extensions['task_scheduler'] = task_scheduler
    app.extensions['user_mgr'] = user_mgr
    app.extensions['device_mgr'] = device_mgr
    app.extensions['env_checker'] = env_checker
    app.extensions['config'] = config
    
    @app.before_request
    def before_req():
        request.start_time = time.time()
    
    @app.after_request
    def after_req(response):
        return response
    
    @app.route('/api/health')
    def health():
        info = task_scheduler.get_queue_info()
        return jsonify({
            "status": "ok",
            "app": APP_NAME,
            "version": APP_VERSION,
            "time": time.time(),
            "queue": info
        })
    
    @app.route('/api/server/info')
    def server_info():
        from common.config import get_local_ip, get_machine_code
        return jsonify({
            "name": APP_NAME,
            "version": APP_VERSION,
            "local_ip": get_local_ip(),
            "access_code": config.get('server', {}).get('access_code', ''),
            "port": config.get('server', {}).get('port', 8989),
            "printers_count": len(printer_mgr.get_all_printers(shared_only=True)),
            "online_devices": len(device_mgr.get_online_devices())
        })
    
    @app.route('/api/device/register', methods=['POST'])
    def device_register():
        data = request.get_json() or {}
        device_id = data.get('device_id', '')
        device_name = data.get('device_name', '')
        mac_address = data.get('mac_address', '')
        os_info = data.get('os_info', '')
        access_code = data.get('access_code', '')
        ip = request.remote_addr
        
        if not device_mgr.verify_access_code(access_code):
            return jsonify({"success": False, "msg": "访问码错误"}), 403
        
        success, msg = device_mgr.register_device(device_id, device_name, ip, mac_address, os_info)
        if not success:
            return jsonify({"success": False, "msg": msg}), 403
        
        return jsonify({
            "success": True,
            "msg": "注册成功",
            "server_name": APP_NAME,
            "printers": printer_mgr.get_all_printers(shared_only=True)
        })
    
    @app.route('/api/device/heartbeat', methods=['POST'])
    def device_heartbeat():
        data = request.get_json() or {}
        device_id = data.get('device_id', '')
        device_mgr.heartbeat(device_id, request.remote_addr)
        return jsonify({"success": True})
    
    @app.route('/api/printers')
    def get_printers():
        device_id = request.args.get('device_id', '')
        if not device_mgr.is_device_allowed(device_id, request.remote_addr):
            return jsonify({"success": False, "msg": "设备未授权"}), 403
        
        printers = printer_mgr.get_all_printers(shared_only=True)
        return jsonify({"success": True, "printers": printers})
    
    @app.route('/api/upload', methods=['POST'])
    def upload_file():
        device_id = request.form.get('device_id', '')
        user_id = request.form.get('user_id')
        username = request.form.get('username', '')
        
        if not device_mgr.is_device_allowed(device_id, request.remote_addr):
            return jsonify({"success": False, "msg": "设备未授权"}), 403
        
        if 'file' not in request.files:
            return jsonify({"success": False, "msg": "未上传文件"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "msg": "文件名为空"}), 400
        
        original_filename = file.filename
        safe_name = original_filename.replace('\\', '_').replace('/', '_').replace('..', '_')
        safe_name = ''.join(c for c in safe_name if c not in '\x00\n\r\t')
        if not safe_name or safe_name.startswith('.'):
            safe_name = f"upload_{int(time.time())}{os.path.splitext(original_filename)[1].lower()}"
        
        ext = os.path.splitext(safe_name)[1].lower()
        
        task_id = gen_task_id()
        saved_name = f"{task_id}{ext}"
        save_path = os.path.join(UPLOAD_DIR, saved_name)
        
        file.save(save_path)
        file_size = os.path.getsize(save_path)
        
        pages = 0
        if ext == '.pdf':
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(save_path)
                pages = len(reader.pages)
            except:
                pass
        
        return jsonify({
            "success": True,
            "task_id": task_id,
            "file_name": safe_name,
            "file_path": save_path,
            "file_size": file_size,
            "pages": pages
        })
    
    @app.route('/api/print/submit', methods=['POST'])
    def submit_print():
        data = request.get_json() or {}
        device_id = data.get('device_id', '')
        
        if not device_mgr.is_device_allowed(device_id, request.remote_addr):
            return jsonify({"success": False, "msg": "设备未授权"}), 403
        
        file_name = data.get('file_name', '')
        file_path = data.get('file_path', '')
        printer_id = data.get('printer_id')
        copies = data.get('copies', 1)
        color_mode = data.get('color_mode', 'black')
        duplex = data.get('duplex', 'simplex')
        paper_size = data.get('paper_size', 'A4')
        page_range = data.get('page_range', '')
        orientation = data.get('orientation', 'portrait')
        margin_top = data.get('margin_top', 0)
        margin_bottom = data.get('margin_bottom', 0)
        margin_left = data.get('margin_left', 0)
        margin_right = data.get('margin_right', 0)
        center_horizontal = data.get('center_horizontal', 0)
        center_vertical = data.get('center_vertical', 0)
        user_id = data.get('user_id')
        username = data.get('username', '')
        device_name = data.get('device_name', '')
        
        printer = printer_mgr.get_printer(printer_id)
        if not printer or not printer['is_shared']:
            return jsonify({"success": False, "msg": "打印机不存在或未共享"}), 404
        
        pages = data.get('pages', 0)
        if user_id and pages > 0:
            if not user_mgr.check_daily_limit(user_id, pages * copies):
                return jsonify({"success": False, "msg": "今日打印页数已达上限"}), 400
        
        task_info = {
            'task_id': data.get('task_id') or gen_task_id(),
            'user_id': user_id,
            'username': username,
            'device_id': device_id,
            'device_name': device_name,
            'printer_id': printer_id,
            'printer_name': printer['name'],
            'file_name': file_name,
            'file_path': file_path,
            'file_size': data.get('file_size', 0),
            'pages': pages,
            'copies': copies,
            'color_mode': color_mode,
            'duplex': duplex,
            'paper_size': paper_size,
            'page_range': page_range,
            'orientation': orientation,
            'margin_top': margin_top,
            'margin_bottom': margin_bottom,
            'margin_left': margin_left,
            'margin_right': margin_right,
            'center_horizontal': center_horizontal,
            'center_vertical': center_vertical,
        }
        
        try:
            task_id = task_scheduler.submit_task(task_info)
            return jsonify({"success": True, "task_id": task_id})
        except Exception as e:
            return jsonify({"success": False, "msg": str(e)}), 500
    
    @app.route('/api/task/status')
    def task_status():
        task_id = request.args.get('task_id', '')
        if not task_id:
            return jsonify({"success": False, "msg": "缺少任务ID"}), 400
        
        task = task_scheduler.get_task_status(task_id)
        if not task:
            return jsonify({"success": False, "msg": "任务不存在"}), 404
        
        return jsonify({"success": True, "task": task})
    
    @app.route('/api/task/cancel', methods=['POST'])
    def task_cancel():
        data = request.get_json() or {}
        task_id = data.get('task_id', '')
        user_id = data.get('user_id')
        
        success, msg = task_scheduler.cancel_task(task_id, user_id)
        return jsonify({"success": success, "msg": msg})
    
    @app.route('/api/task/retry', methods=['POST'])
    def task_retry():
        data = request.get_json() or {}
        task_id = data.get('task_id', '')
        
        success, result = task_scheduler.retry_task(task_id)
        if success:
            return jsonify({"success": True, "new_task_id": result})
        return jsonify({"success": False, "msg": result}), 400
    
    @app.route('/api/user/tasks')
    def user_tasks():
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({"success": False, "msg": "缺少用户ID"}), 400
        
        tasks = task_scheduler.get_user_tasks(int(user_id))
        return jsonify({"success": True, "tasks": tasks})
    
    @app.route('/api/device/tasks')
    def device_tasks():
        device_id = request.args.get('device_id', '')
        if not device_id:
            return jsonify({"success": False, "msg": "缺少设备ID"}), 400
        
        if not device_mgr.is_device_allowed(device_id, request.remote_addr):
            return jsonify({"success": False, "msg": "设备未授权"}), 403
        
        tasks = task_scheduler.get_device_tasks(device_id)
        return jsonify({"success": True, "tasks": tasks})
    
    @app.route('/api/user/tasks/clear', methods=['POST'])
    def clear_user_tasks():
        data = request.get_json() or {}
        user_id = data.get('user_id')
        status = data.get('status')
        if not user_id:
            return jsonify({"success": False, "msg": "缺少用户ID"}), 400
        
        count = task_scheduler.clear_user_tasks(int(user_id), status)
        return jsonify({"success": True, "count": count})
    
    @app.route('/api/device/tasks/clear', methods=['POST'])
    def clear_device_tasks():
        data = request.get_json() or {}
        device_id = data.get('device_id', '')
        status = data.get('status')
        if not device_id:
            return jsonify({"success": False, "msg": "缺少设备ID"}), 400
        
        if not device_mgr.is_device_allowed(device_id, request.remote_addr):
            return jsonify({"success": False, "msg": "设备未授权"}), 403
        
        count = task_scheduler.clear_device_tasks(device_id, status)
        return jsonify({"success": True, "count": count})
    
    @app.route('/api/user/login', methods=['POST'])
    def user_login():
        data = request.get_json() or {}
        username = data.get('username', '')
        password = data.get('password', '')
        
        user, msg = user_mgr.authenticate(username, password)
        if user:
            return jsonify({
                "success": True,
                "user": {
                    "id": user['id'],
                    "username": user['username'],
                    "role": user['role'],
                    "real_name": user['real_name'],
                    "department": user['department']
                }
            })
        return jsonify({"success": False, "msg": msg}), 401
    
    @app.route('/api/env/check')
    def env_check():
        results = env_checker.check_all()
        return jsonify({"success": True, "results": results})

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
        color_mode = data.get('color_mode', '')
        duplex = data.get('duplex', 'simplex')

        pid = printer_mgr.add_printer(
            name=name,
            connection_type=conn_type,
            is_shared=is_shared,
            is_default=is_default,
            paper_size=paper_size,
            color_mode=color_mode if color_mode else None,
            duplex=duplex,
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
    def cancel_task_web(task_id):
        task_scheduler.cancel_task(task_id)
        return redirect(url_for('web_tasks'))
    
    @app.route('/tasks/<task_id>/retry')
    @login_required
    def retry_task_web(task_id):
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

_server_thread = None
_server_app = None
_server_running = False
_server_port = 8989
_shutdown_func = None

def start_server(port=8989, existing_managers=None):
    global _server_thread, _server_app, _server_running, _server_port, _shutdown_func
    if _server_running:
        return
    
    config = load_config()
    port = config.get("server", {}).get("port", port)
    _server_port = port
    
    _server_app = create_app(config, existing_managers)
    
    def run_server():
        global _server_running, _shutdown_func
        _server_running = True
        try:
            from werkzeug.serving import make_server
            server = make_server('0.0.0.0', port, _server_app, threaded=True)
            _shutdown_func = server.shutdown
            server.serve_forever()
        except Exception as e:
            logger.error(f"Server error: {e}")
        finally:
            _server_running = False
            _shutdown_func = None
    
    _server_thread = threading.Thread(target=run_server, daemon=True)
    _server_thread.start()
    time.sleep(0.5)
    logger.info(f"Server started on port {port}")

def stop_server():
    global _server_thread, _server_app, _server_running, _shutdown_func
    if _shutdown_func:
        try:
            _shutdown_func()
        except Exception:
            pass
    if _server_app:
        try:
            ts = _server_app.extensions.get('task_scheduler')
            if ts:
                ts.stop()
        except Exception:
            pass
    _server_running = False
    _shutdown_func = None
    logger.info("Server stopped")

def get_server_status():
    global _server_running, _server_port
    return {
        'running': _server_running,
        'port': _server_port
    }

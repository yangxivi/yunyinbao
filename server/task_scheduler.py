import os
import sys
import time
import threading
import queue
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TaskScheduler:
    MAX_AUTO_RETRIES = 2  # 打印失败后自动重试次数（首次失败后可再自动重试 2 次）

    def __init__(self, db, printer_manager, print_engine):
        self.db = db
        self.printer_manager = printer_manager
        self.print_engine = print_engine
        self.task_queue = queue.Queue()
        self.running = False
        self.worker_thread = None
        self.current_task = None
        self._lock = threading.Lock()
        self._status_callbacks = []
        self._retry_counts = {}  # task_id -> 已自动重试次数

    def start(self):
        if self.running:
            return
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        threading.Thread(target=self._load_pending_tasks, daemon=True).start()
        logger.info("任务调度器已启动")

    def stop(self):
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        logger.info("任务调度器已停止")

    def _load_pending_tasks(self):
        try:
            conn = self.db.get_db()
            rows = conn.execute(
                "SELECT * FROM print_tasks WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
            conn.close()
            for row in rows:
                self.task_queue.put(row['task_id'])
            logger.info(f"加载了 {len(rows)} 个待处理任务")
        except Exception as e:
            logger.error(f"加载待处理任务失败: {e}")

    def _worker_loop(self):
        while self.running:
            try:
                self._clean_expired_tasks()
                task_id = self.task_queue.get(timeout=1)
                self._process_task(task_id)
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"任务处理异常: {e}")
                time.sleep(1)

    def _process_task(self, task_id):
        try:
            conn = self.db.get_db()
            row = conn.execute(
                "SELECT * FROM print_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            
            if not row:
                conn.close()
                return
            
            if row['status'] != 'pending':
                conn.close()
                return
            
            if row['expires_at'] and row['expires_at'] < time.time():
                conn.execute(
                    "UPDATE print_tasks SET status = 'expired' WHERE task_id = ?",
                    (task_id,)
                )
                conn.commit()
                conn.close()
                self._notify_status(task_id, 'expired')
                return
            
            with self._lock:
                self.current_task = task_id
            
            conn.execute(
                "UPDATE print_tasks SET status = 'printing', started_at = ? WHERE task_id = ?",
                (time.time(), task_id)
            )
            conn.commit()
            self._notify_status(task_id, 'printing')
            
            printer_name = row['printer_name']
            file_path = row['file_path']
            copies = row['copies']
            color_mode = row['color_mode']
            duplex = row['duplex']
            paper_size = row['paper_size']
            page_range = row['page_range']
            orientation = row['orientation'] if 'orientation' in row.keys() else 'portrait'
            margin_top = row['margin_top'] if 'margin_top' in row.keys() else 0
            margin_bottom = row['margin_bottom'] if 'margin_bottom' in row.keys() else 0
            margin_left = row['margin_left'] if 'margin_left' in row.keys() else 0
            margin_right = row['margin_right'] if 'margin_right' in row.keys() else 0
            center_horizontal = row['center_horizontal'] if 'center_horizontal' in row.keys() else 0
            center_vertical = row['center_vertical'] if 'center_vertical' in row.keys() else 0

            conn.close()

            if not os.path.exists(file_path):
                self._update_task_status(task_id, 'failed', '文件不存在')
                self._notify_status(task_id, 'failed', '文件不存在')
                return

            success, msg = self.print_engine.print_file(
                file_path, printer_name, copies, color_mode, duplex, paper_size, page_range,
                orientation, margin_top, margin_bottom, margin_left, margin_right,
                center_horizontal, center_vertical
            )
            
            if success:
                pages = row['pages'] or 0
                if pages > 0:
                    self._update_user_pages(row['user_id'], pages * copies)
                
                self._retry_counts.pop(task_id, None)
                self._update_task_status(task_id, 'completed', msg)
                self._notify_status(task_id, 'completed', msg)
                self._add_log(task_id, row['user_id'], 'print_complete', f"打印完成: {msg}")
            else:
                # 失败自动重试：未超过次数则重新排队，否则标记失败
                retry_count = self._retry_counts.get(task_id, 0)
                if retry_count < self.MAX_AUTO_RETRIES:
                    self._retry_counts[task_id] = retry_count + 1
                    self._update_task_status(task_id, 'pending', f'自动重试第 {retry_count + 1} 次: {msg}')
                    self._notify_status(task_id, 'retrying', msg)
                    self._add_log(task_id, row['user_id'], 'retry',
                                  f"打印失败，自动重试 {retry_count + 1}/{self.MAX_AUTO_RETRIES}: {msg}")
                    self.task_queue.put(task_id)
                    with self._lock:
                        self.current_task = None
                    return
                self._retry_counts.pop(task_id, None)
                self._update_task_status(task_id, 'failed', msg)
                self._notify_status(task_id, 'failed', msg)
                self._add_log(task_id, row['user_id'], 'print_failed', f"打印失败: {msg}")
            
            with self._lock:
                self.current_task = None
                
        except Exception as e:
            logger.error(f"处理任务 {task_id} 失败: {e}")
            self._update_task_status(task_id, 'failed', str(e))
            self._notify_status(task_id, 'failed', str(e))
            with self._lock:
                self.current_task = None

    def submit_task(self, task_info):
        try:
            conn = self.db.get_db()
            now = time.time()
            expires = now + 24 * 3600
            
            conn.execute(
                """INSERT INTO print_tasks
                (task_id, user_id, username, device_id, device_name, printer_id, printer_name,
                 file_name, file_path, file_size, pages, copies, color_mode, duplex,
                 paper_size, page_range, orientation, margin_top, margin_bottom, margin_left,
                 margin_right, center_horizontal, center_vertical, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_info['task_id'],
                    task_info.get('user_id'),
                    task_info.get('username', ''),
                    task_info.get('device_id', ''),
                    task_info.get('device_name', ''),
                    task_info.get('printer_id'),
                    task_info.get('printer_name', ''),
                    task_info.get('file_name', ''),
                    task_info.get('file_path', ''),
                    task_info.get('file_size', 0),
                    task_info.get('pages', 0),
                    task_info.get('copies', 1),
                    task_info.get('color_mode', 'black'),
                    task_info.get('duplex', 'simplex'),
                    task_info.get('paper_size', 'A4'),
                    task_info.get('page_range', ''),
                    task_info.get('orientation', 'portrait'),
                    task_info.get('margin_top', 0),
                    task_info.get('margin_bottom', 0),
                    task_info.get('margin_left', 0),
                    task_info.get('margin_right', 0),
                    task_info.get('center_horizontal', 0),
                    task_info.get('center_vertical', 0),
                    'pending',
                    now,
                    expires
                )
            )
            conn.commit()
            conn.close()
            
            self.task_queue.put(task_info['task_id'])
            self._add_log(task_info['task_id'], task_info.get('user_id'), 'submit', '任务已提交')
            
            return task_info['task_id']
        except Exception as e:
            logger.error(f"提交任务失败: {e}")
            raise

    def cancel_task(self, task_id, user_id=None):
        try:
            conn = self.db.get_db()
            row = conn.execute(
                "SELECT * FROM print_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            
            if not row:
                conn.close()
                return False, "任务不存在"
            
            if user_id and row['user_id'] != user_id:
                conn.close()
                return False, "无权取消此任务"
            
            if row['status'] not in ('pending',):
                conn.close()
                return False, "任务已开始处理，无法取消"
            
            conn.execute(
                "UPDATE print_tasks SET status = 'cancelled', completed_at = ? WHERE task_id = ?",
                (time.time(), task_id)
            )
            conn.commit()
            conn.close()
            
            self._notify_status(task_id, 'cancelled')
            self._add_log(task_id, row['user_id'], 'cancel', '任务已取消')
            
            return True, "已取消"
        except Exception as e:
            return False, str(e)

    def retry_task(self, task_id):
        try:
            conn = self.db.get_db()
            row = conn.execute(
                "SELECT * FROM print_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            
            if not row:
                conn.close()
                return False, "任务不存在"
            
            new_task_id = f"R{row['task_id']}"[-20:]
            now = time.time()
            expires = now + 24 * 3600
            
            conn.execute(
                """INSERT INTO print_tasks
                (task_id, user_id, username, device_id, device_name, printer_id, printer_name,
                 file_name, file_path, file_size, pages, copies, color_mode, duplex,
                 paper_size, page_range, orientation, margin_top, margin_bottom, margin_left,
                 margin_right, center_horizontal, center_vertical, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_task_id, row['user_id'], row['username'], row['device_id'],
                    row['device_name'], row['printer_id'], row['printer_name'], row['file_name'],
                    row['file_path'], row['file_size'], row['pages'], row['copies'],
                    row['color_mode'], row['duplex'], row['paper_size'],
                    row['page_range'],
                    row['orientation'] if 'orientation' in row.keys() else 'portrait',
                    row['margin_top'] if 'margin_top' in row.keys() else 0,
                    row['margin_bottom'] if 'margin_bottom' in row.keys() else 0,
                    row['margin_left'] if 'margin_left' in row.keys() else 0,
                    row['margin_right'] if 'margin_right' in row.keys() else 0,
                    row['center_horizontal'] if 'center_horizontal' in row.keys() else 0,
                    row['center_vertical'] if 'center_vertical' in row.keys() else 0,
                    'pending', now, expires
                )
            )
            conn.commit()
            conn.close()
            
            self.task_queue.put(new_task_id)
            return True, new_task_id
        except Exception as e:
            return False, str(e)

    def get_task_status(self, task_id):
        try:
            conn = self.db.get_db()
            row = conn.execute(
                "SELECT * FROM print_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            return None

    def get_user_tasks(self, user_id, limit=50):
        try:
            conn = self.db.get_db()
            rows = conn.execute(
                "SELECT * FROM print_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            return []

    def get_device_tasks(self, device_id, limit=50):
        """按设备ID查询任务列表"""
        try:
            conn = self.db.get_db()
            rows = conn.execute(
                "SELECT * FROM print_tasks WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                (device_id, limit)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"查询设备任务失败: {e}")
            return []

    def clear_user_tasks(self, user_id, status=None):
        """清空用户任务（不删除等待中和正在打印的任务）"""
        try:
            conn = self.db.get_db()
            safe_statuses = ('completed', 'failed', 'cancelled', 'expired')
            if status:
                if status in safe_statuses:
                    cursor = conn.execute(
                        "DELETE FROM print_tasks WHERE user_id = ? AND status = ?",
                        (user_id, status)
                    )
                else:
                    conn.close()
                    return 0
            else:
                placeholders = ','.join('?' * len(safe_statuses))
                cursor = conn.execute(
                    f"DELETE FROM print_tasks WHERE user_id = ? AND status IN ({placeholders})",
                    (user_id,) + safe_statuses
                )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception as e:
            logger.error(f"清空用户任务失败: {e}")
            return 0

    def clear_device_tasks(self, device_id, status=None):
        """清空设备任务（不删除等待中和正在打印的任务）"""
        try:
            conn = self.db.get_db()
            safe_statuses = ('completed', 'failed', 'cancelled', 'expired')
            if status:
                if status in safe_statuses:
                    cursor = conn.execute(
                        "DELETE FROM print_tasks WHERE device_id = ? AND status = ?",
                        (device_id, status)
                    )
                else:
                    conn.close()
                    return 0
            else:
                placeholders = ','.join('?' * len(safe_statuses))
                cursor = conn.execute(
                    f"DELETE FROM print_tasks WHERE device_id = ? AND status IN ({placeholders})",
                    (device_id,) + safe_statuses
                )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception as e:
            logger.error(f"清空设备任务失败: {e}")
            return 0

    def get_all_tasks(self, status=None, limit=100):
        try:
            conn = self.db.get_db()
            if status:
                rows = conn.execute(
                    "SELECT * FROM print_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM print_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            return []

    def _update_task_status(self, task_id, status, error_msg=None):
        try:
            conn = self.db.get_db()
            now = time.time()
            if status in ('completed', 'failed', 'cancelled', 'expired'):
                conn.execute(
                    "UPDATE print_tasks SET status = ?, error_msg = ?, completed_at = ? WHERE task_id = ?",
                    (status, error_msg, now, task_id)
                )
            else:
                conn.execute(
                    "UPDATE print_tasks SET status = ?, error_msg = ? WHERE task_id = ?",
                    (status, error_msg, task_id)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"更新任务状态失败: {e}")

    def _update_user_pages(self, user_id, pages):
        if not user_id:
            return
        try:
            conn = self.db.get_db()
            today = time.strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            
            if row:
                if row['last_reset_date'] != today:
                    conn.execute(
                        "UPDATE users SET today_pages = ?, last_reset_date = ? WHERE id = ?",
                        (pages, today, user_id)
                    )
                else:
                    conn.execute(
                        "UPDATE users SET today_pages = today_pages + ? WHERE id = ?",
                        (pages, user_id)
                    )
                conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"更新用户页数失败: {e}")

    def _clean_expired_tasks(self):
        try:
            conn = self.db.get_db()
            now = time.time()
            conn.execute(
                "UPDATE print_tasks SET status = 'expired' WHERE status = 'pending' AND expires_at < ?",
                (now,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            pass

    def _add_log(self, task_id, user_id, action, detail):
        try:
            conn = self.db.get_db()
            conn.execute(
                "INSERT INTO logs (task_id, user_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, user_id, action, detail, time.time())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            pass

    def register_status_callback(self, callback):
        self._status_callbacks.append(callback)

    def _notify_status(self, task_id, status, detail=""):
        for cb in self._status_callbacks:
            try:
                cb(task_id, status, detail)
            except Exception as e:
                pass

    def get_queue_info(self):
        conn = self.db.get_db()
        pending = conn.execute("SELECT COUNT(*) as c FROM print_tasks WHERE status = 'pending'").fetchone()['c']
        printing = conn.execute("SELECT COUNT(*) as c FROM print_tasks WHERE status = 'printing'").fetchone()['c']
        today_total = conn.execute(
            "SELECT COUNT(*) as c FROM print_tasks WHERE date(created_at, 'unixepoch', 'localtime') = date('now', 'localtime')"
        ).fetchone()['c']
        conn.close()
        return {
            "pending": pending,
            "printing": printing,
            "today_total": today_total,
            "current_task": self.current_task
        }

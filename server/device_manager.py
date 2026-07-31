import os
import time
import threading
import logging
import platform
import socket
import uuid

logger = logging.getLogger(__name__)

class DeviceManager:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self._lock = threading.Lock()
        self.online_devices = {}
        self._heartbeat_timeout = 60

    def register_device(self, device_id, device_name="", ip_address="", mac_address="", os_info=""):
        with self._lock:
            try:
                conn = self.db.get_db()
                now = self.db.now_ts()
                
                existing = conn.execute(
                    "SELECT * FROM devices WHERE device_id = ?", (device_id,)
                ).fetchone()
                
                if existing:
                    if existing['blocked']:
                        conn.close()
                        return False, "设备已被拉黑"
                    
                    conn.execute(
                        """UPDATE devices SET 
                        device_name = ?, ip_address = ?, mac_address = ?, os_info = ?, 
                        last_online = ?, status = 'online'
                        WHERE device_id = ?""",
                        (device_name, ip_address, mac_address, os_info, now, device_id)
                    )
                else:
                    conn.execute(
                        """INSERT INTO devices 
                        (device_id, device_name, ip_address, mac_address, os_info, last_online, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'online', ?)""",
                        (device_id, device_name, ip_address, mac_address, os_info, now, now)
                    )
                
                conn.commit()
                conn.close()
                
                self.online_devices[device_id] = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "ip_address": ip_address,
                    "last_online": now,
                    "status": "online"
                }
                
                return True, "注册成功"
            except Exception as e:
                return False, str(e)

    def heartbeat(self, device_id, ip_address=""):
        try:
            now = time.time()
            conn = self.db.get_db()
            conn.execute(
                "UPDATE devices SET last_online = ?, status = 'online', ip_address = ? WHERE device_id = ?",
                (now, ip_address, device_id)
            )
            conn.commit()
            conn.close()
            
            if device_id in self.online_devices:
                self.online_devices[device_id]['last_online'] = now
                self.online_devices[device_id]['ip_address'] = ip_address
            
            return True
        except Exception as e:
            return False

    def is_device_allowed(self, device_id, ip_address=""):
        try:
            security = self.config.get('security', {})
            if security.get('use_whitelist') and security.get('whitelist'):
                if ip_address not in security['whitelist']:
                    return False
            
            conn = self.db.get_db()
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            conn.close()
            
            if row and row['blocked']:
                return False
            
            return True
        except:
            return True

    def get_online_devices(self):
        now = time.time()
        online = []
        for did, info in self.online_devices.items():
            if now - info['last_online'] < self._heartbeat_timeout:
                online.append(info)
        return online

    def get_all_devices(self):
        try:
            conn = self.db.get_db()
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY last_online DESC"
            ).fetchall()
            conn.close()
            
            now = time.time()
            result = []
            for r in rows:
                d = dict(r)
                d['is_online'] = (now - d['last_online']) < self._heartbeat_timeout
                result.append(d)
            return result
        except:
            return []

    def block_device(self, device_id):
        with self._lock:
            try:
                conn = self.db.get_db()
                conn.execute(
                    "UPDATE devices SET blocked = 1, status = 'blocked' WHERE device_id = ?",
                    (device_id,)
                )
                conn.commit()
                conn.close()
                
                if device_id in self.online_devices:
                    del self.online_devices[device_id]
                
                return True
            except:
                return False

    def unblock_device(self, device_id):
        with self._lock:
            try:
                conn = self.db.get_db()
                conn.execute(
                    "UPDATE devices SET blocked = 0, status = 'online' WHERE device_id = ?",
                    (device_id,)
                )
                conn.commit()
                conn.close()
                return True
            except:
                return False

    def delete_device(self, device_id):
        with self._lock:
            try:
                conn = self.db.get_db()
                conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
                conn.commit()
                conn.close()
                
                if device_id in self.online_devices:
                    del self.online_devices[device_id]
                
                return True
            except:
                return False

    def kick_device(self, device_id):
        if device_id in self.online_devices:
            del self.online_devices[device_id]
        return True

    def verify_access_code(self, code):
        access_code = self.config.get('server', {}).get('access_code', '')
        return code == access_code

    def cleanup_offline(self):
        now = time.time()
        to_remove = []
        for did, info in list(self.online_devices.items()):
            if now - info['last_online'] > self._heartbeat_timeout * 2:
                to_remove.append(did)
        for did in to_remove:
            del self.online_devices[did]

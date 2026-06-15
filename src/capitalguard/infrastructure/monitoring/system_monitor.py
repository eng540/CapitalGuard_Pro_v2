# src/capitalguard/infrastructure/monitoring/system_monitor.py (New File)
"""
System Monitor - مراقبة شاملة لأداء النظام
"""

import asyncio
import logging
import time
import psutil
from typing import Dict, Any

log = logging.getLogger(__name__)

class SystemMonitor:
    """مراقب أداء النظام"""
    
    def __init__(self, alert_service=None, check_interval: int = 60):
        self.alert_service = alert_service
        self.check_interval = check_interval
        self._task = None
        self._is_running = False
        
    async def check_system_health(self) -> Dict[str, Any]:
        """فحص صحة النظام"""
        try:
            # استخدام الذاكرة
            memory = psutil.virtual_memory()
            # استخدام CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            # استخدام القرص
            disk = psutil.disk_usage('/')
            
            health_info = {
                'timestamp': time.time(),
                'memory_used_percent': memory.percent,
                'memory_used_gb': round(memory.used / (1024**3), 2),
                'memory_total_gb': round(memory.total / (1024**3), 2),
                'cpu_percent': cpu_percent,
                'disk_used_percent': disk.percent,
                'disk_free_gb': round(disk.free / (1024**3), 2)
            }
            
            # تحذيرات إذا تجاوزت الحدود
            warnings = []
            if memory.percent > 80:
                warnings.append(f"High memory usage: {memory.percent}%")
            if cpu_percent > 85:
                warnings.append(f"High CPU usage: {cpu_percent}%")
            if disk.percent > 90:
                warnings.append(f"High disk usage: {disk.percent}%")
                
            health_info['warnings'] = warnings
            
            return health_info
            
        except Exception as e:
            log.error("❌ System health check failed: %s", e)
            return {'error': str(e)}
    
    async def _monitor_loop(self):
        """حلقة المراقبة"""
        while self._is_running:
            try:
                health = await self.check_system_health()
                
                if health.get('warnings'):
                    log.warning("⚠️ System warnings: %s", health['warnings'])
                    
                # سجل حالة النظام كل 5 دقائق
                if int(time.time()) % 300 < self.check_interval:
                    log.info("📊 System health: MEM=%d%%, CPU=%d%%, DISK=%d%%", 
                            health['memory_used_percent'], health['cpu_percent'], health['disk_used_percent'])
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                log.error("❌ Monitor loop error: %s", e)
                await asyncio.sleep(self.check_interval)
    
    def start(self):
        """بدء المراقبة"""
        if self._is_running:
            return
            
        self._is_running = True
        self._task = asyncio.create_task(self._monitor_loop())
        log.info("✅ System monitor started")
    
    def stop(self):
        """إيقاف المراقبة"""
        self._is_running = False
        if self._task:
            self._task.cancel()
        log.info("🛑 System monitor stopped")
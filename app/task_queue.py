import asyncio
import logging
from typing import Callable, Any, Dict
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class TaskQueue:
    """
    Асинхронная очередь задач с ограничением количества одновременных обработок.
    """
    
    def __init__(self, max_concurrent_tasks: int = 3):
        self.max_concurrent_tasks = max_concurrent_tasks
        self.queue = asyncio.Queue()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_results: Dict[str, Any] = {}
        self._is_running = False
        self._worker_task = None
    
    async def add_task(self, task_func: Callable, *args, **kwargs) -> str:
        """
        Добавляет задачу в очередь.
        
        Args:
            task_func: Функция для выполнения
            *args: Аргументы функции
            **kwargs: Ключевые аргументы функции
            
        Returns:
            str: ID задачи
        """
        task_id = str(uuid.uuid4())
        task_data = {
            'id': task_id,
            'func': task_func,
            'args': args,
            'kwargs': kwargs,
            'created_at': datetime.now(),
            'status': 'queued'
        }
        
        await self.queue.put(task_data)
        self.task_results[task_id] = {'status': 'queued', 'created_at': task_data['created_at']}
        
        logger.info(f"Задача {task_id} добавлена в очередь. Размер очереди: {self.queue.qsize()}")
        return task_id
    
    async def _worker(self):
        """Воркер, который обрабатывает задачи из очереди."""
        while self._is_running:
            try:
                # Ждем задачу из очереди
                task_data = await self.queue.get()
                task_id = task_data['id']
                
                # Проверяем, не превысили ли лимит одновременных задач
                while len(self.active_tasks) >= self.max_concurrent_tasks:
                    await asyncio.sleep(0.1)
                
                # Запускаем задачу
                self.task_results[task_id].update({
                    'status': 'processing',
                    'started_at': datetime.now()
                })
                
                task = asyncio.create_task(
                    self._execute_task(task_data)
                )
                self.active_tasks[task_id] = task
                
                # Убираем задачу из очереди
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в воркере: {e}")
    
    async def _execute_task(self, task_data: Dict):
        """Выполняет задачу и сохраняет результат."""
        task_id = task_data['id']
        try:
            result = await task_data['func'](*task_data['args'], **task_data['kwargs'])
            
            self.task_results[task_id].update({
                'status': 'completed',
                'completed_at': datetime.now(),
                'result': result,
                'error': None
            })
            
            logger.info(f"Задача {task_id} успешно выполнена")
            
        except Exception as e:
            self.task_results[task_id].update({
                'status': 'failed',
                'completed_at': datetime.now(),
                'result': None,
                'error': str(e)
            })
            
            logger.error(f"Задача {task_id} завершилась с ошибкой: {e}")
        
        finally:
            # Убираем задачу из активных
            self.active_tasks.pop(task_id, None)
    
    def get_task_status(self, task_id: str) -> Dict:
        """
        Возвращает статус задачи.
        
        Args:
            task_id: ID задачи
            
        Returns:
            Dict: Информация о задаче
        """
        return self.task_results.get(task_id, {'status': 'not_found'})
    
    def get_queue_stats(self) -> Dict:
        """
        Возвращает статистику очереди.
        
        Returns:
            Dict: Статистика
        """
        return {
            'queue_size': self.queue.qsize(),
            'active_tasks': len(self.active_tasks),
            'total_tasks': len(self.task_results),
            'max_concurrent': self.max_concurrent_tasks
        }
    
    async def start(self):
        """Запускает обработчик очереди."""
        if not self._is_running:
            self._is_running = True
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("Очередь задач запущена")
    
    async def stop(self):
        """Останавливает обработчик очереди."""
        if self._is_running:
            self._is_running = False
            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            logger.info("Очередь задач остановлена")

# Глобальный экземпляр очереди задач
task_queue = TaskQueue(max_concurrent_tasks=2)  # Обрабатываем 2 задачи одновременно
# app/task_queue.py
import asyncio
import logging
from typing import Callable, Any, Dict, Tuple
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class TaskQueue:
    """
    Асинхронная очередь задач с приоритетом.
    Меньшее число = выше приоритет (0 — PRO/админ, 1 — обычный).
    """

    def __init__(self, max_concurrent_tasks: int = 3):
        self.max_concurrent_tasks = max_concurrent_tasks
        self.queue: asyncio.PriorityQueue[Tuple[int, int, Dict[str, Any]]] = asyncio.PriorityQueue()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_results: Dict[str, Any] = {}
        self._is_running = False
        self._worker_task = None
        self._seq = 0  # для стабильного порядка при одинаковом приоритете

    async def add_task(self, task_func: Callable, *args, priority: int = 1, **kwargs) -> str:
        """
        Добавляет задачу в очередь.
        priority: 0 — высокий (PRO/админ), 1 — обычный.
        """
        task_id = str(uuid.uuid4())
        task_data = {
            "id": task_id,
            "func": task_func,
            "args": args,
            "kwargs": kwargs,
            "created_at": datetime.now(),
            "status": "queued",
            "priority": int(priority),
        }
        self.task_results[task_id] = {"status": "queued", "created_at": task_data["created_at"], "priority": int(priority)}
        self._seq += 1
        await self.queue.put((int(priority), self._seq, task_data))
        logger.info(f"Задача {task_id} добавлена (priority={priority}). Очередь: {self.queue.qsize()}")
        return task_id

    async def _worker(self):
        """Воркер, который обрабатывает задачи из очереди."""
        while self._is_running:
            try:
                priority, _seq, task_data = await self.queue.get()
                task_id = task_data["id"]

                # ждём, если пробита параллельность
                while len(self.active_tasks) >= self.max_concurrent_tasks:
                    await asyncio.sleep(0.05)

                self.task_results[task_id].update({"status": "processing", "started_at": datetime.now()})
                task = asyncio.create_task(self._execute_task(task_data))
                self.active_tasks[task_id] = task

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в воркере: {e}")

    async def _execute_task(self, task_data: Dict):
        """Выполняет задачу и сохраняет результат."""
        task_id = task_data["id"]
        try:
            result = await task_data["func"](*task_data["args"], **task_data["kwargs"])
            self.task_results[task_id].update(
                {"status": "completed", "completed_at": datetime.now(), "result": result, "error": None}
            )
            logger.info(f"Задача {task_id} выполнена")
        except Exception as e:
            self.task_results[task_id].update(
                {"status": "failed", "completed_at": datetime.now(), "result": None, "error": str(e)}
            )
            logger.error(f"Задача {task_id} завершилась с ошибкой: {e}")
        finally:
            self.active_tasks.pop(task_id, None)

    def get_task_status(self, task_id: str) -> Dict:
        return self.task_results.get(task_id, {"status": "not_found"})

    def get_queue_stats(self) -> Dict:
        return {
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "total_tasks": len(self.task_results),
            "max_concurrent": self.max_concurrent_tasks,
        }

    async def start(self):
        if not self._is_running:
            self._is_running = True
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("Очередь задач запущена")

    async def stop(self):
        if self._is_running:
            self._is_running = False
            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            logger.info("Очередь задач остановлена")

# PRO по умолчанию — выше приоритет
task_queue = TaskQueue(max_concurrent_tasks=2)

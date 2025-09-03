# app/task_queue.py
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass(order=True)
class _PQItem:
    """
    Элемент очереди для heapq: сортируем по (prio, created_ts, seq).
    Чем меньше prio — тем выше приоритет (PRO = 0).
    """
    prio: int
    created_ts: float
    seq: int
    id: str = field(compare=False)
    func: Callable = field(compare=False)
    args: Tuple = field(compare=False)
    kwargs: Dict = field(compare=False)


class TaskQueue:
    """
    Асинхронная очередь задач с *настоящим приоритетом* и ограничением числа
    одновременных обработок.

    Публичное API:
      - add_task(func, *args, priority=1, **kwargs) -> task_id
      - get_task_status(task_id) -> Dict
      - get_queue_stats() -> Dict
      - cancel(task_id) -> bool
      - start() / stop()
    """

    def __init__(self, max_concurrent_tasks: int = 3):
        self.max_concurrent_tasks = max_concurrent_tasks

        # heapq как список; защищаем простым asyncio.Lock
        self._heap: list[_PQItem] = []
        self._heap_lock = asyncio.Lock()
        self._seq = 0  # для стабильной сортировки при одинаковом времени

        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_results: Dict[str, Any] = {}

        self._is_running = False
        self._worker_task: asyncio.Task | None = None

    async def add_task(self, task_func: Callable, *args, priority: int = 1, **kwargs) -> str:
        """
        Добавляет задачу с приоритетом (0 — выше, 1 — ниже).
        Возвращает task_id.
        """
        task_id = str(uuid.uuid4())
        created_ts = time.time()

        async with self._heap_lock:
            self._seq += 1
            item = _PQItem(
                prio=int(priority),
                created_ts=created_ts,
                seq=self._seq,
                id=task_id,
                func=task_func,
                args=args,
                kwargs=kwargs,
            )
            import heapq
            heapq.heappush(self._heap, item)

        self.task_results[task_id] = {
            "status": "queued",
            "created_at": datetime.fromtimestamp(created_ts),
            "priority": int(priority),
        }

        logger.info("Задача %s добавлена: prio=%s, heap_size=%d",
                    task_id, priority, len(self._heap))
        return task_id

    async def _worker(self):
        """Воркер: запускает задачи из приоритетной кучи, учитывая лимит concurrency."""
        logger.info("Воркер очереди запущен")
        while self._is_running:
            try:
                # Ждём свободный слот
                if len(self.active_tasks) >= self.max_concurrent_tasks:
                    await asyncio.sleep(0.05)
                    continue

                # Достаём следующий элемент очереди
                async with self._heap_lock:
                    if not self._heap:
                        item = None
                    else:
                        import heapq
                        item = heapq.heappop(self._heap)

                if not item:
                    # Пусто — подождать.
                    await asyncio.sleep(0.05)
                    continue

                task_id = item.id
                self.task_results[task_id].update({
                    "status": "processing",
                    "started_at": datetime.now(),
                })

                # Запускаем обработку
                task = asyncio.create_task(self._execute_task(item))
                self.active_tasks[task_id] = task

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ошибка в воркере очереди: %s", e, exc_info=True)
                await asyncio.sleep(0.1)

        logger.info("Воркер очереди остановлен")

    async def _execute_task(self, item: _PQItem):
        """Выполняет задачу и сохраняет результат/ошибку."""
        task_id = item.id
        try:
            result = await item.func(*item.args, **item.kwargs)
            self.task_results[task_id].update({
                "status": "completed",
                "completed_at": datetime.now(),
                "result": result,
                "error": None,
            })
            logger.info("Задача %s выполнена", task_id)
        except asyncio.CancelledError:
            self.task_results[task_id].update({
                "status": "canceled",
                "completed_at": datetime.now(),
                "result": None,
                "error": "canceled",
            })
            logger.warning("Задача %s отменена во время выполнения", task_id)
        except Exception as e:
            self.task_results[task_id].update({
                "status": "failed",
                "completed_at": datetime.now(),
                "result": None,
                "error": str(e),
            })
            logger.error("Задача %s завершилась с ошибкой: %s", task_id, e, exc_info=True)
        finally:
            self.active_tasks.pop(task_id, None)

    def get_task_status(self, task_id: str) -> Dict:
        """Возвращает информацию о задаче (если нет — status=not_found)."""
        return self.task_results.get(task_id, {"status": "not_found"})

    def get_queue_stats(self) -> Dict:
        """Короткая статистика по очереди."""
        return {
            "queue_size": len(self._heap),
            "active_tasks": len(self.active_tasks),
            "total_tasks": len(self.task_results),
            "max_concurrent": self.max_concurrent_tasks,
        }

    def cancel(self, task_id: str) -> bool:
        """
        Отменяет задачу, если она в очереди или в активной обработке.
        Возвращает True, если получилось отменить.
        """
        # 1) Попробуем вытащить из кучи
        removed = False
        try:
            # Линейный поиск в куче — потом heapify.
            for idx, it in enumerate(self._heap):
                if it.id == task_id:
                    self._heap.pop(idx)
                    import heapq
                    heapq.heapify(self._heap)
                    removed = True
                    break
        except Exception:
            pass

        if removed:
            self.task_results[task_id]["status"] = "canceled"
            self.task_results[task_id]["completed_at"] = datetime.now()
            logger.info("Задача %s отменена (из очереди)", task_id)
            return True

        # 2) Если уже выполняется — отменяем asyncio.Task
        task = self.active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info("Задача %s запрошена к отмене (в работе)", task_id)
            return True

        return False

    async def start(self):
        """Запускает воркер очереди (если ещё не запущен)."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        """Останавливает воркер очереди и дожидается корректного завершения."""
        if not self._is_running:
            return
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

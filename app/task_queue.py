# app/task_queue.py
import asyncio
import os
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Dict, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass(order=True)
class _PQItem:
    """
    Элемент очереди (min-heap) с сортировкой по: (prio, created_ts, seq).
    Меньший prio -> выше приоритет (например, PRO=0).
    """
    prio: int
    created_ts: float
    seq: int
    id: str = field(compare=False)
    func: Callable = field(compare=False)
    args: Tuple = field(compare=False)
    kwargs: Dict = field(compare=False)
    timeout: Optional[float] = field(default=None, compare=False)  # сек, опционально


class TaskQueue:
    """
    Асинхронная очередь задач с настоящим приоритетом и лимитом concurrency.

    Публичное API:
      - add_task(func, *args, priority=1, _timeout=None, **kwargs) -> task_id
      - get_task_status(task_id) -> Dict
      - get_task_position(task_id) -> int | None
      - get_queue_stats() -> Dict
      - cancel(task_id) -> bool
      - purge_old_results(max_items=5000) -> None
      - start() / stop(graceful=True, cancel_active=False)
    """

    def __init__(self, max_concurrent_tasks: int = 3):
        self.max_concurrent_tasks = max_concurrent_tasks

        # приоритетная куча + блокировка
        self._heap: list[_PQItem] = []
        self._heap_lock = asyncio.Lock()
        self._seq = 0

        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_results: Dict[str, Dict[str, Any]] = {}

        # id задач, которые пользователь попросил отменить
        self._canceled_ids: set[str] = set()

        self._is_running = False
        self._worker_task: Optional[asyncio.Task] = None

    async def add_task(
        self,
        task_func: Callable,
        *args,
        priority: int = 1,
        _timeout: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Добавляет задачу. priority: 0 (высокий), 1 (обычный).
        _timeout (сек): опциональный таймаут выполнения (через asyncio.wait_for).
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
                timeout=_timeout,
            )
            import heapq
            heapq.heappush(self._heap, item)
            heap_size = len(self._heap)

        self.task_results[task_id] = {
            "status": "queued",
            "created_at": datetime.fromtimestamp(created_ts),
            "priority": int(priority),
            "func_name": getattr(task_func, "__name__", str(task_func)),
        }

        logger.info("Задача %s добавлена: prio=%s, heap_size=%d", task_id, priority, heap_size)

        # Чтобы не разрасталась история (опционально)
        self.purge_old_results(max_items=5000)

        return task_id

    async def _worker(self):
        logger.info("Воркер очереди запущен")
        try:
            while self._is_running:
                try:
                    # ждём свободный слот
                    if len(self.active_tasks) >= self.max_concurrent_tasks:
                        await asyncio.sleep(0.05)
                        continue

                    # достаём из кучи следующую задачу
                    async with self._heap_lock:
                        if not self._heap:
                            item = None
                        else:
                            import heapq
                            item = heapq.heappop(self._heap)

                    if not item:
                        await asyncio.sleep(0.05)
                        continue

                    # если задачу успели отменить — пропускаем
                    if item.id in self._canceled_ids:
                        self._canceled_ids.discard(item.id)
                        # если уже есть запись — обновим статус; если нет — создадим
                        rec = self.task_results.setdefault(item.id, {"created_at": datetime.now()})
                        rec.update({"status": "canceled", "completed_at": datetime.now(), "result": None, "error": "canceled"})
                        logger.info("Задача %s пропущена (была отменена до старта)", item.id)
                        continue

                    task_id = item.id
                    self.task_results[task_id].update({
                        "status": "processing",
                        "started_at": datetime.now(),
                    })

                    # запускаем обработчик
                    coro = self._execute_task(item)
                    task = asyncio.create_task(coro)
                    self.active_tasks[task_id] = task

                except asyncio.CancelledError:
                    # корректное завершение воркера
                    break
                except Exception as e:
                    logger.error("Ошибка в воркере очереди: %s", e, exc_info=True)
                    await asyncio.sleep(0.1)
        finally:
            logger.info("Воркер очереди остановлен")

    async def _execute_task(self, item: _PQItem):
        """Выполняет задачу и фиксирует итоговый статус/результат."""
        task_id = item.id
        try:
            if item.timeout is not None and item.timeout > 0:
                result = await asyncio.wait_for(item.func(*item.args, **item.kwargs), timeout=item.timeout)
            else:
                result = await item.func(*item.args, **item.kwargs)

            self.task_results[task_id].update({
                "status": "completed",
                "completed_at": datetime.now(),
                "result": result,
                "error": None,
            })
            logger.info("Задача %s выполнена", task_id)

        except asyncio.TimeoutError:
            self.task_results[task_id].update({
                "status": "failed",
                "completed_at": datetime.now(),
                "result": None,
                "error": f"timeout({item.timeout}s)",
            })
            logger.warning("Задача %s прервана по таймауту %ss", task_id, item.timeout)
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

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Текущий статус задачи (или {'status': 'not_found'})."""
        return self.task_results.get(task_id, {"status": "not_found"})

    def get_queue_stats(self) -> Dict[str, int]:
        """Короткая статистика по очереди."""
        return {
            "queue_size": len(self._heap),
            "active_tasks": len(self.active_tasks),
            "total_tasks": len(self.task_results),
            "max_concurrent": self.max_concurrent_tasks,
        }

    def get_task_position(self, task_id: str) -> Optional[int]:
        """
        Позиция задачи в очереди (0 — следующая к запуску).
        Если задача не в очереди (выполняется/завершена/нет) — None.
        """
        items = list(self._heap)  # копия без лока — достаточная точность для UI
        if not items:
            return None
        items_sorted = sorted(items)  # использует порядок _PQItem
        for idx, it in enumerate(items_sorted):
            if it.id == task_id:
                return idx
        return None

    def cancel(self, task_id: str) -> bool:
        """
        Отменяет задачу, если она в очереди или выполняется.
        True — если отменили.
        """
        # 1) пытаемся убрать из кучи (без await — мы в одном треде event loop)
        for idx, it in enumerate(self._heap):
            if it.id == task_id:
                self._heap.pop(idx)
                import heapq
                heapq.heapify(self._heap)
                rec = self.task_results.setdefault(task_id, {"created_at": datetime.now()})
                rec.update({"status": "canceled", "completed_at": datetime.now(), "result": None, "error": "canceled"})
                logger.info("Задача %s отменена (из очереди)", task_id)
                return True

        # 2) если уже выполняется — отменяем asyncio.Task
        task = self.active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info("Задача %s запрошена к отмене (в работе)", task_id)
            return True

        # 3) возможно, воркер уже вытащил из кучи, но ещё не поставил статус.
        # Пометим как отменённую — воркер увидит и пропустит.
        if self.task_results.get(task_id, {}).get("status") in {"queued", "processing"}:
            self._canceled_ids.add(task_id)
            logger.info("Задача %s помечена как отменённая (ожидает в воркере)", task_id)
            return True

        return False

    def purge_old_results(self, max_items: int = 5000) -> None:
        """
        Ограничивает размер истории результатов, оставляя последние max_items.
        """
        n = len(self.task_results)
        if n <= max_items:
            return
        items = list(self.task_results.items())
        items.sort(key=lambda kv: kv[1].get("created_at", datetime.min))
        for task_id, _ in items[: n - max_items]:
            self.task_results.pop(task_id, None)

    async def start(self):
        """Запускает воркер (идемпотентно)."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self, graceful: bool = True, cancel_active: bool = False):
        """
        Останавливает очередь.
        graceful=True — перестаём брать новые задачи и ждём завершения воркера.
        cancel_active=True — принудительно отменить активные задачи.
        """
        if not self._is_running:
            return
        self._is_running = False

        # останавливаем воркер
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # по желанию отменяем активные задачи
        if cancel_active:
            for tid, t in list(self.active_tasks.items()):
                if not t.done():
                    t.cancel()
            await asyncio.sleep(0)

        if graceful and self.active_tasks:
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)


# --- Singleton-инстанс очереди для всего бота (ВАЖНО: на уровне модуля) ---
try:
    _MAXC = int(os.getenv("TASKQ_MAX_CONCURRENCY", "3"))
except Exception:
    _MAXC = 3

task_queue = TaskQueue(max_concurrent_tasks=_MAXC)

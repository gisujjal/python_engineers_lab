"""A small timer-based event loop with a custom gather() awaitable.

event_loop.run() drives coroutines by sending values into them. Sleep yields a
delay so the scheduler can pause that coroutine and run another one. gather()
asks the scheduler to run child coroutines and returns their results in input
order. The scheduler uses time.sleep() and supports timers only, not async I/O.
"""

import time
from collections.abc import Coroutine, Iterable
from typing import Any, List, Optional, Tuple, Union


class _GatherRequest:
    def __init__(self, coroutines: Tuple[Coroutine, ...]):
        self.coroutines = coroutines

    def __await__(self):
        results = yield self
        return results


class _GatherState:
    def __init__(
        self,
        parent: Coroutine,
        parent_group: Optional["_GatherState"],
        parent_index: Optional[int],
        coroutines: Tuple[Coroutine, ...],
    ):
        self.parent = parent
        self.parent_group = parent_group
        self.parent_index = parent_index
        self.results: List[Any] = [None] * len(coroutines)
        self.remaining = len(coroutines)
        self.error: Optional[Exception] = None

    def complete(self, index: int, result: Any, error: Optional[Exception]):
        self.results[index] = result
        if error is not None and self.error is None:
            self.error = error
        self.remaining -= 1
        if self.remaining:
            return None
        return (
            self.parent,
            self.parent_group,
            self.parent_index,
            self.results,
            self.error,
        )


async def _await_gather(request: _GatherRequest):
    return await request


def gather(*coroutines: Union[Coroutine, _GatherRequest]) -> _GatherRequest:
    """Return an awaitable that runs coroutines concurrently in this event loop."""
    tasks = tuple(
        _await_gather(coroutine)
        if isinstance(coroutine, _GatherRequest)
        else coroutine
        for coroutine in coroutines
    )
    return _GatherRequest(tasks)


class event_loop:
    def run(
        self, *coroutines: Union[Coroutine, Iterable[Coroutine]]
    ) -> None:
        tasks: List[Coroutine] = []
        for item in coroutines:
            if isinstance(item, Coroutine):
                tasks.append(item)
            else:
                tasks.extend(item)

        if any(not isinstance(task, Coroutine) for task in tasks):
            raise TypeError("run() accepts coroutine objects or iterables of coroutines")

        queue = [
            (time.monotonic(), task, None, None, None)
            for task in tasks
        ]

        def complete_child(group, result, error):
            state, index = group
            parent = state.complete(index, result, error)
            if parent is not None:
                parent_coro, parent_group, parent_index, results, parent_error = parent
                queue.append(
                    (
                        time.monotonic(),
                        parent_coro,
                        results,
                        parent_error,
                        (parent_group, parent_index)
                        if parent_group is not None
                        else None,
                    )
                )

        while queue:
            next_index = min(range(len(queue)), key=lambda index: queue[index][0])
            wake_time, coro, send_value, error, group = queue.pop(next_index)
            delay = wake_time - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                yielded = coro.throw(error) if error is not None else coro.send(send_value)
            except StopIteration as finished:
                if group is not None:
                    complete_child(group, finished.value, None)
                continue
            except Exception as exception:
                if group is None:
                    raise
                complete_child(group, None, exception)
                continue

            if isinstance(yielded, _GatherRequest):
                child_coroutines = yielded.coroutines
                if any(not isinstance(child, Coroutine) for child in child_coroutines):
                    raise TypeError("gather() accepts coroutine objects only")
                if not child_coroutines:
                    queue.append((time.monotonic(), coro, [], None, group))
                    continue

                state = _GatherState(
                    coro,
                    group[0] if group is not None else None,
                    group[1] if group is not None else None,
                    child_coroutines,
                )
                for index, child in enumerate(child_coroutines):
                    queue.append(
                        (time.monotonic(), child, None, None, (state, index))
                    )
                continue

            if not isinstance(yielded, (int, float)):
                raise TypeError(
                    "This event loop supports only Sleep and its custom gather()"
                )
            if yielded < 0:
                raise ValueError("Sleep duration must be non-negative")

            queue.append(
                (time.monotonic() + yielded, coro, None, None, group)
            )


class Sleep:
    def __init__(self, seconds: float):
        self.seconds = seconds

    def __await__(self):
        yield self.seconds
        return None


async def worker(name: str, delay: float) -> str:
    print(f"{name} started")
    await Sleep(delay)
    print(f"{name} finished")
    return name



async def main():
    results = await gather(
        worker("A", 2),
        worker("B", 1),
        worker("C", 1.5),
    )
    print(f"Results: {results}")


if __name__ == "__main__":
    start_time = time.monotonic()
    event_loop().run(main())
    print(f"Execution time: {time.monotonic() - start_time:.2f}")

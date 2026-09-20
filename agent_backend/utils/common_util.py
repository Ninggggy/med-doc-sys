import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from typing import Any, Callable


class ResponseMessage:
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "data": self.data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def parallelize_processing(field_to_iterate: str, result_field: str, max_workers: int = 8):
    """
    Generic parallel helper for list fields in a state dict.
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(self, data_state):
            items = data_state.get(field_to_iterate, [])
            if not isinstance(items, list):
                raise ValueError(f"{field_to_iterate} must be a list")

            result_list = [None] * len(items)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(func, self, data_state, item, idx): idx for idx, item in enumerate(items)}
                for future in as_completed(futures):
                    idx = futures[future]
                    result_list[idx] = future.result()
            data_state[result_field] = result_list
            return data_state

        return wrapper

    return decorator


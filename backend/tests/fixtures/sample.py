"""Sample Python file for symbol extraction tests."""

import os
from pathlib import Path


def top_level_function(x: int) -> int:
    return x + 1


async def async_top_level():
    pass


class MyClass:
    def method_one(self):
        pass

    async def async_method(self, value):
        return value

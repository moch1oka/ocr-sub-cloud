import sys

if sys.version_info >= (3, 12):
    from typing import override
else:
    try:
        from typing_extensions import override
    except ImportError:
        def override(func):
            return func

from rich.progress import ProgressColumn, Task
from rich.text import Text


class ImageSecondSpeedColumn(ProgressColumn):
    @override
    def render(self, task: Task) -> Text:
        return Text(f"{task.speed or 0:.02f} images/s")
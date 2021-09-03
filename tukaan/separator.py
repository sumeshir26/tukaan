from typing import Literal, Optional

from ._base import BaseWidget, TukaanWidget


class Separator(BaseWidget):
    _keys = {"orientation": (str, "orient")}

    def __init__(
        self,
        parent: Optional[TukaanWidget] = None,
        orientation: Optional[Literal["horizontal", "vertical"]] = None,
    ) -> None:
        BaseWidget.__init__(self, parent, "ttk::separator", orient=orientation)

    def _repr_details(self):
        return f"orientation={self.orientation!r}"

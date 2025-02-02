from __future__ import annotations

import collections
import re
from fractions import Fraction
from typing import Callable, Tuple, cast

from PIL import ImageGrab  # type: ignore

from ._platform import Platform
from ._utils import (
    ClassPropertyMetaClass,
    _flatten,
    _fonts,
    _pairs,
    classproperty,
    counts,
    from_tcl,
    get_tcl_interp,
    py_to_tcl_arguments,
    reversed_dict,
    to_tcl,
    update_after,
)
from .exceptions import ColorError, TclError

intround: Callable[[float], int] = lambda x: int(round(x, 0))
round4: Callable[[float], float] = lambda x: round(x, 4)


class HEX:
    @staticmethod
    def to_hex(r, g, b) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def from_hex(hex) -> tuple[int, ...]:
        int_value = int(hex.lstrip("#"), 16)
        return cast(Tuple[int, ...], (int_value >> 16, int_value >> 8 & 0xFF, int_value & 0xFF))


class HSL:
    @staticmethod
    def to_hsl(r, g, b) -> tuple[int, ...]:
        r, g, b = r / 255, g / 255, b / 255
        min_value = min(r, g, b)
        max_value = max(r, g, b)

        l = (min_value + max_value) / 2

        if min_value == max_value:
            return (0, 0, intround(l * 100))

        if l <= 0.5:
            s = (max_value - min_value) / (max_value + min_value)
        elif l > 0.5:
            s = (max_value - min_value) / (2.0 - max_value - min_value)

        if max_value == r:
            h = (g - b) / (max_value - min_value)
        elif max_value == g:
            h = 2.0 + (b - g) / (max_value - min_value)
        elif max_value == b:
            h = 4.0 + (r - g) / (max_value - min_value)

        return tuple(intround(x) for x in (h * 60, s * 100, l * 100))

    @staticmethod
    def from_hsl(h, s, l) -> tuple[int, ...]:
        h, s, l = h / 360, s / 100, l / 100

        if s == 0:
            return (intround(l * 255),) * 3

        if l >= 0.5:
            tmp_1 = l + s - l * s
        elif l < 0.5:
            tmp_1 = l * (1 + s)

        tmp_2 = 2 * l - tmp_1

        def func(h):
            h = h % 1
            if h < 1 / 6:
                return tmp_2 + (tmp_1 - tmp_2) * h * 6
            if h < 0.5:
                return tmp_1
            if h < 2 / 3:
                return tmp_2 + (tmp_1 - tmp_2) * (2 / 3 - h) * 6
            return tmp_2

        r = func(h + 1 / 3)
        g = func(h)
        b = func(h - 1 / 3)

        return tuple(intround(x) for x in (r * 255, g * 255, b * 255))


class HSV:
    @staticmethod
    def to_hsv(r, g, b) -> tuple[int, ...]:
        r, g, b = tuple(x / 255 for x in (r, g, b))

        high = max(r, g, b)
        low = min(r, g, b)
        diff = high - low

        h = 0
        s = 0 if high == 0 else (diff / high) * 100
        v = high * 100

        if high == r:
            h = (60 * ((g - b) / diff) + 360) % 360
        elif high == g:
            h = (60 * ((b - r) / diff) + 120) % 360
        elif high == b:
            h = (60 * ((r - g) / diff) + 240) % 360

        return cast(Tuple[int, ...], tuple(intround(x) for x in (h, s, v)))

    @staticmethod
    def from_hsv(h, s, v) -> tuple[int, ...]:
        h, s, v = h / 360, s / 100, v / 100

        if s == 0.0:
            return cast(Tuple[int, ...], tuple(intround(x * 255) for x in (v, v, v)))

        i = int(h * 6.0)
        f = (h * 6.0) - i

        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))

        r, g, b = (
            (v, t, p),
            (q, v, p),
            (p, v, t),
            (p, q, v),
            (t, p, v),
            (v, p, q),
        )[int(i % 6)]

        return cast(Tuple[int, ...], tuple(intround(x * 255) for x in (r, g, b)))


class CMYK:
    @staticmethod
    def to_cmyk(r, g, b) -> tuple[int, ...]:
        if (r, g, b) == (0, 0, 0):
            return (0, 0, 0, 100)

        c, m, y = (1 - x / 255 for x in (r, g, b))

        k = min(c, m, y)
        c = c - k
        m = m - k
        y = y - k

        return cast(Tuple[int, ...], tuple(intround(x * 100) for x in (c, m, y, k)))

    @staticmethod
    def from_cmyk(c, m, y, k) -> tuple[int, ...]:
        c = c / 100.0
        m = m / 100.0
        y = y / 100.0
        k = k / 100.0

        r = 255.0 - ((min(1.0, c * (1.0 - k) + k)) * 255.0)
        g = 255.0 - ((min(1.0, m * (1.0 - k) + k)) * 255.0)
        b = 255.0 - ((min(1.0, y * (1.0 - k) + k)) * 255.0)

        return cast(Tuple[int, ...], tuple(intround(x) for x in (r, g, b)))


# TODO: hsl, yiq
class Color:
    _supported_color_spaces = {"hex", "rgb", "hsv", "cmyk", "hsl"}
    _length_dict = {"rgb": 3, "hsv": 3, "cmyk": 4, "hsl": 3}
    _maximum_values = {
        "rgb": (255,) * 3,
        "hsv": (360, 100, 100),
        "hsl": (360, 100, 100),
        "cmyk": (100,) * 4,
    }

    red: int
    green: int
    blue: int

    def __init__(self, name: str = None, **kwargs) -> None:
        # FIXME: this is a HUGE mess
        if len(kwargs) > 1:
            raise ValueError("too many keyword arguments. 1 expected.")

        if name and kwargs:
            raise ValueError("a single color name, OR a keyword argument is expected.")

        color: tuple | str
        if name is None:
            color = tuple(kwargs.values())[0]
        else:
            color = name

        if name:
            space = "hex"
        else:
            space = tuple(kwargs.keys())[0]

        try:
            if space == "hex":
                if not re.match(r"^#[0-9a-fA-F]{6}$", color):
                    raise ColorError
                self.red, self.green, self.blue = HEX.from_hex(color)

            elif len(color) == self._length_dict[space] and self._check_in_range(
                space, color, opt="str"
            ):
                if space == "rgb":
                    assert not isinstance(color, str)
                    self.red, self.green, self.blue = color
                elif space == "hsl":
                    self.red, self.green, self.blue = HSL.from_hsl(*color)
                elif space == "hsv":
                    self.red, self.green, self.blue = HSV.from_hsv(*color)
                elif space == "cmyk":
                    self.red, self.green, self.blue = CMYK.from_cmyk(*color)
            else:
                raise ColorError

        except (ColorError, KeyError):
            # error checking is still too boilerplaty
            raise ColorError(self._what_is_the_problem(name, kwargs)) from None

    def _check_in_range(self, space, color, opt=None):
        for limit, number in zip(self._maximum_values[space], color):
            if not (0 <= number <= limit):
                return False
        return True

    def _what_is_the_problem(self, str_name, kwargs) -> str:

        if str_name and not kwargs:
            return f"invalid color name: {str_name!r}"

        color = tuple(kwargs.values())[0]
        space = tuple(kwargs.keys())[0]

        if space not in self._supported_color_spaces:
            return f"unknown keywords argument: {space}"

        elif not isinstance(color, tuple):
            return f"{color!r} is not a valid {space} color. A tuple is expected."

        else:
            color_passed, expected_length = "", ""

            if len(color) in self._length_dict.values():

                if len(color) == 4 and self._check_in_range("cmyk", color):
                    color_space = "a cmyk"

                elif len(color) == 3 and (0,) * 3 <= color <= (255, 100, 100):
                    color_space = "either a rgb, a hsl or a hsv"

                elif len(color) == 3 and self._check_in_range("rgb", color):
                    color_space = "a rgb"

                elif len(color) == 3 and (0,) * 3 <= color <= (360, 100, 100):
                    color_space = "either a hsl or a hsv"

                else:
                    color_space = "an invalid"

                color_passed = f" You passed in {color_space} color."

            if len(color) != self._length_dict[space]:
                expected_length = f"A tuple of length of {self._length_dict[space]} is expected."

            return f"{color!r} is not a valid {space} color." + expected_length + color_passed

        return "not implemented tukaan.Color error."  # shouldn't get here

    def __repr__(self) -> str:
        return f"{type(self).__name__}(red={self.red}, green={self.green}," + f" blue={self.blue})"

    def to_tcl(self) -> str:
        return self.hex

    __str__ = to_tcl

    @classmethod
    def from_tcl(cls, tcl_value) -> Color:
        return cls(tcl_value)

    def invert(self) -> Color:
        self.red = 255 - self.red
        self.green = 255 - self.green
        self.blue = 255 - self.blue

        return self

    def mix(self, other, ratio):
        if not isinstance(other, Color):
            raise TypeError

        a, b = Fraction.from_float(ratio).as_integer_ratio()
        amount_of_clr_1 = 1 / (a + b) * a
        amount_of_clr_2 = 1 / (a + b) * b

        r, g, b = (
            round(amount_of_clr_1 * value1 + amount_of_clr_2 * value2)
            for value1, value2 in zip(self.rgb, other.rgb)
        )
        return Color(rgb=(r, g, b))

    def __or__(self, other):
        return self.mix(other, 1 / 1)

    @property
    def is_dark(self):
        # https://www.w3schools.com/lib/w3color.js line 82
        return ((self.red * 299 + self.green * 587 + self.blue * 114) / 1000) < 128

    @property
    def hex(self) -> str:
        return HEX.to_hex(self.red, self.green, self.blue)

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.red, self.green, self.blue)

    @property
    def hsv(self) -> tuple:
        return HSV.to_hsv(self.red, self.green, self.blue)

    @property
    def cmyk(self) -> tuple:
        return CMYK.to_cmyk(self.red, self.green, self.blue)


class Clipboard(metaclass=ClassPropertyMetaClass):
    @classmethod
    def __repr__(cls) -> str:
        return f"{type(cls).__name__}(content={cls.get()})"

    @classmethod
    def clear(cls) -> None:
        get_tcl_interp()._tcl_call(None, "clipboard", "clear")

    @classmethod
    def append(cls, content) -> None:
        get_tcl_interp()._tcl_call(None, "clipboard", "append", content)

    def __add__(self, content) -> Clipboard:
        self.append(content)
        return self

    @classmethod
    def get(cls) -> str | None:
        try:
            return get_tcl_interp()._tcl_call(str, "clipboard", "get")
        except TclError:
            try:
                return ImageGrab.grabclipboard()
            except NotImplementedError:
                # grabclipboard() is macOS and Windows only
                return None

    @classmethod
    def set(cls, new_content: str) -> None:
        get_tcl_interp()._tcl_call(None, "clipboard", "clear")
        get_tcl_interp()._tcl_call(None, "clipboard", "append", new_content)

    @classproperty
    def content(cls) -> str:
        return cls.get()

    @content.setter
    def content(cls, new_content: str) -> None:
        cls.set(new_content)


class Cursor(collections.namedtuple("Cursor", "cursor"), metaclass=ClassPropertyMetaClass):
    _cursor_dict: dict[str | None, str] = {
        "crosshair": "crosshair",
        "default": "arrow",
        "e-resize": "right_side",
        "help": "question_arrow",
        "move": "fleur",
        "n-resize": "top_side",
        "ne-sw-resize": "top_right_corner",
        "not-allowed": "circle",
        "ns-resize": "sb_v_double_arrow",
        "nw-se-resize": "top_left_corner",
        "pointer": "hand2",
        "progress": "arrow",  # for cross-platform compatibility
        "s-resize": "bottom_side",
        "text": "xterm",
        "w-resize": "left_side",
        "wait": "watch",
        "we-resize": "sb_h_double_arrow",
        None: "none",
    }

    _win_cursor_dict: dict[str | None, str] = {
        "not-allowed": "no",
        "progress": "starting",
        "ne-sw-resize": "size_ne_sw",
        "ns-resize": "size_ns",
        "nw-se-resize": "size_nw_se",
        "wait": "wait",
        "we-resize": "size_we",
    }

    if Platform.system == "Windows":
        _cursor_dict = {**_cursor_dict, **_win_cursor_dict}

    def to_tcl(self) -> str:
        return self._cursor_dict[self.cursor]

    @classmethod
    def from_tcl(cls, tcl_value: str) -> Cursor:
        return cls(reversed_dict(cls._cursor_dict)[tcl_value])

    @classproperty
    def x(cls) -> int:
        return get_tcl_interp()._tcl_call(int, "winfo", "pointerx", ".")

    @x.setter
    @update_after
    def x(cls, new_x: int) -> None:
        get_tcl_interp()._tcl_call(
            None,
            "event",
            "generate",
            ".",
            "<Motion>",
            "-warp",
            "1",
            "-x",
            new_x,
            "-y",
            cls.y,
        )

    @classproperty
    def y(cls) -> int:
        return get_tcl_interp()._tcl_call(int, "winfo", "pointery", ".")

    @y.setter
    @update_after
    def y(cls, new_y: int) -> None:
        get_tcl_interp()._tcl_call(
            None,
            "event",
            "generate",
            ".",
            "<Motion>",
            "-warp",
            "1",
            "-y",
            new_y,
            "-x",
            cls.x,
        )

    @classproperty
    def position(cls) -> tuple[int, int]:
        return (cls.x, cls.y)

    @position.setter
    @update_after
    def position(cls, new_pos: int | tuple[int, int] | list[int]) -> None:
        if isinstance(new_pos, (tuple, list)) and len(new_pos) > 1:
            x, y = new_pos
        elif isinstance(new_pos, int):
            x = y = new_pos
        else:
            raise RuntimeError

        get_tcl_interp()._tcl_call(
            None, "event", "generate", ".", "<Motion>", "-warp", "1", "-x", x, "-y", y
        )


common_resolution_standards = {
    (1024, 600): "WSVGA",
    (1024, 768): "XGA",
    (1280, 1024): "SXGA",
    (1280, 720): "HD 720",
    (1280, 768): "WXGA",
    (1280, 800): "WXGA",
    (1400, 1050): "SXGA+",
    (1600, 1200): "UXGA",
    (1680, 1050): "WSXGA+",
    (1920, 1080): "HD 1080",
    (1920, 1200): "WUXGA",
    (2048, 1080): "2K",
    (2048, 1536): "QXGA",
    (2560, 1600): "WQXGA",
    (2560, 2048): "QSXGA",
    (320, 200): "CGA",
    (320, 240): "QVGA",
    (640, 480): "VGA",
    (768, 576): "PAL",
    (800, 480): "WVGA",
    (800, 600): "SVGA",
    (854, 480): "FWVGA",
}

common_aspect_ratios = {
    16 / 10: "16:10",
    16 / 9: "16:9",
    17 / 9: "17:9",
    3 / 2: "3:2",
    4 / 3: "4:3",
    5 / 3: "5:3",
    5 / 4: "5:4",
}

common_color_depths = {
    1: "monochrome",
    15: "high color",
    16: "high color",
    24: "true color",
    30: "deep color",
    36: "deep color",
    48: "deep color",
}


class Screen(metaclass=ClassPropertyMetaClass):
    @classproperty
    def _width(cls) -> ScreenDistance:
        return get_tcl_interp()._tcl_call(int, "winfo", "screenwidth", ".")

    @classproperty
    def _height(cls) -> ScreenDistance:
        return get_tcl_interp()._tcl_call(int, "winfo", "screenheight", ".")

    @classproperty
    def width(cls) -> ScreenDistance:
        return ScreenDistance(cls._width)

    @classproperty
    def height(cls) -> ScreenDistance:
        return ScreenDistance(cls._height)

    @classproperty
    def size(cls) -> tuple[ScreenDistance, ScreenDistance]:
        return (ScreenDistance(cls._width), ScreenDistance(cls._height))

    @classproperty
    def area(cls) -> ScreenDistance:
        return ScreenDistance(cls._width * cls._height)

    @classproperty
    def aspect_ratio(cls) -> str:
        try:
            return common_aspect_ratios[cls._width / cls._height]
        except KeyError:
            fraction = Fraction(cls._width, cls._height)  # reduce the ratio
            return f"{fraction.numerator}:{fraction.denominator}"

    @classproperty
    def resolution_standard(cls) -> str:
        try:
            return common_resolution_standards[(cls._width, cls._height)]
        except KeyError:
            return ""

    @classproperty
    def diagonal(cls) -> int:
        return ScreenDistance((cls._width**2 + cls._height**2) ** 0.5)  # pythagoras

    @classproperty
    def color_depth(cls) -> int:
        return get_tcl_interp()._tcl_call(int, "winfo", "screendepth", ".")

    @classproperty
    def color_depth_alias(cls) -> str:
        try:
            return common_color_depths[cls.color_depth]
        except KeyError:
            return ""

    @classproperty
    def dpi(cls) -> float:
        return get_tcl_interp()._tcl_call(float, "winfo", "fpixels", ".", "1i")

    @classproperty
    def ppi(cls) -> float:
        return cls.dpi

    def __str__(self) -> str:
        return f"{self.width.px};{self.height.px}"


class ScreenDistance(collections.namedtuple("ScreenDistance", "distance")):
    """An object to convert between different screen distance units"""

    _tcl_units = {"px": "", "mm": "m", "cm": "c", "inch": "i"}

    def __new__(cls, px=0, mm=0, cm=0, inch=0) -> ScreenDistance:
        distance = 0

        for unit, amount in {"cm": cm, "px": px, "mm": mm, "inch": inch}.items():
            pixels = 0

            if amount:
                if unit != "px":
                    pixels = get_tcl_interp()._tcl_call(
                        float, "winfo", "fpixels", ".", f"{amount}{cls._tcl_units[unit]}"
                    )
                else:
                    pixels = float(amount)

            distance += pixels

        cls.dpi = Screen.ppi

        return super(ScreenDistance, cls).__new__(cls, distance)  # type: ignore

    def __repr__(self) -> str:
        return f"{type(self).__name__}(distance={self.distance}px))"

    def __int__(self):
        return intround(self.distance)

    def __float__(self):
        return round4(self.distance)

    def to_tcl(self) -> str:
        return str(self.distance)

    __str__ = to_tcl

    @classmethod
    def from_tcl(cls, tcl_value: str) -> ScreenDistance:
        unit = tcl_value[-1]

        if unit == "c":
            return cls(cm=int(tcl_value[:-1]))
        if unit == "m":
            return cls(mm=int(tcl_value[:-1]))
        if unit == "i":
            return cls(inch=int(tcl_value[:-1]))

        return cls(px=int(tcl_value[:-1]))

    @property
    def px(self) -> float:
        return round4(self.distance)

    @property
    def mm(self) -> float:
        return round4(self.distance / (self.dpi / 25.4))

    @property
    def cm(self) -> float:
        return round4(self.distance / (self.dpi / 2.54))

    @property
    def inch(self) -> float:
        return round4(self.distance / self.dpi)

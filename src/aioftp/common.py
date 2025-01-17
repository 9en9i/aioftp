import abc
import asyncio
import functools
import locale
import threading
from contextlib import contextmanager
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncContextManager,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    Final,
    Generator,
    Generic,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    overload,
)

from typing_extensions import ParamSpec, Self, override

from .types import AsyncEnterableProtocol
from .utils import get_param

if TYPE_CHECKING:
    from .pathio import AsyncPathIO

__all__ = (
    "with_timeout",
    "StreamIO",
    "Throttle",
    "StreamThrottle",
    "ThrottleStreamIO",
    "END_OF_LINE",
    "DEFAULT_BLOCK_SIZE",
    "wrap_with_container",
    "AsyncStreamIterator",
    "AbstractAsyncLister",
    "AsyncListerMixin",
    "async_enterable",
    "DEFAULT_PORT",
    "DEFAULT_USER",
    "DEFAULT_PASSWORD",
    "DEFAULT_ACCOUNT",
    "setlocale",
)


END_OF_LINE: Final[str] = "\r\n"
DEFAULT_BLOCK_SIZE: Final[int] = 8192

DEFAULT_PORT: Final[int] = 21
DEFAULT_USER: Final[str] = "anonymous"
DEFAULT_PASSWORD: Final[str] = "anon@"
DEFAULT_ACCOUNT: Final[str] = ""
HALF_OF_YEAR_IN_SECONDS: Final[int] = 15778476
TWO_YEARS_IN_SECONDS: Final[float] = ((365 * 3 + 366) * 24 * 60 * 60) / 2

_T = TypeVar("_T")
_PS = ParamSpec("_PS")


def _now() -> float:
    return asyncio.get_running_loop().time()


def _with_timeout(
    name: str,
) -> Callable[
    [Callable[_PS, Coroutine[None, None, _T]]],
    Callable[_PS, Coroutine[None, None, _T]],
]:
    def decorator(f: Callable[_PS, Coroutine[None, None, _T]]) -> Callable[_PS, Coroutine[None, None, _T]]:
        @functools.wraps(f)
        async def wrapper(*args: _PS.args, **kwargs: _PS.kwargs) -> _T:
            self: "AsyncPathIO" = get_param((0, "self"), args, kwargs)
            coro = f(*args, **kwargs)
            timeout = getattr(self, name)
            return await asyncio.wait_for(coro, timeout)

        return wrapper

    return decorator


@overload
def with_timeout(
    name: str,
) -> Callable[
    [Callable[_PS, Coroutine[None, None, _T]]],
    Callable[_PS, Coroutine[None, None, _T]],
]: ...


@overload
def with_timeout(
    name: Callable[_PS, Coroutine[None, None, _T]],
) -> Callable[_PS, Coroutine[None, None, _T]]: ...


def with_timeout(
    name: Union[str, Callable[_PS, Coroutine[None, None, _T]]],
) -> Union[
    Callable[
        [Callable[_PS, Coroutine[None, None, _T]]],
        Callable[_PS, Coroutine[None, None, _T]],
    ],
    Callable[_PS, Coroutine[None, None, _T]],
]:
    """
    Method decorator, wraps method with :py:func:`asyncio.wait_for`. `timeout`
    argument takes from `name` decorator argument or "timeout".

    :param name: name of timeout attribute
    :type name: :py:class:`str`

    :raises asyncio.TimeoutError: if coroutine does not finished in timeout

    Wait for `self.timeout`
    ::

        >>> def __init__(self, ...):
        ...
        ...     self.timeout = 1
        ...
        ... @with_timeout
        ... async def foo(self, ...):
        ...
        ...     pass

    Wait for custom timeout
    ::

        >>> def __init__(self, ...):
        ...
        ...     self.foo_timeout = 1
        ...
        ... @with_timeout("foo_timeout")
        ... async def foo(self, ...):
        ...
        ...     pass

    """

    if isinstance(name, str):
        return _with_timeout(name)
    else:
        return _with_timeout("timeout")(name)


class AsyncStreamIterator(AsyncIterator[_T], Generic[_T]):
    def __init__(self, read_coro: Callable[[], Awaitable[_T]]):
        self.read_coro = read_coro

    @override
    def __aiter__(self) -> "AsyncStreamIterator[_T]":
        return self

    @override
    async def __anext__(self) -> _T:
        data = await self.read_coro()
        if data:
            return data
        else:
            raise StopAsyncIteration


class AsyncListerMixin(
    AsyncIterable[_T],
    Awaitable[List[_T]],
    Generic[_T],
):
    """
    Add ability to `async for` context to collect data to list via await.

    ::

        >>> class Context(AsyncListerMixin):
        ...     ...
        >>> results = await Context(...)
    """

    async def _to_list(self) -> List[_T]:
        items: List[_T] = []
        async for item in self:
            items.append(item)
        return items

    @override
    def __await__(self) -> Generator[None, None, List[_T]]:
        return self._to_list().__await__()


class AbstractAsyncLister(AsyncListerMixin[_T], abc.ABC):
    """
    Abstract context with ability to collect all iterables into
    :py:class:`list` via `await` with optional timeout (via
    :py:func:`aioftp.with_timeout`)

    :param timeout: timeout for __anext__ operation
    :type timeout: :py:class:`None`, :py:class:`int` or :py:class:`float`

    ::

        >>> class Lister(AbstractAsyncLister):
        ...
        ...     @with_timeout
        ...     async def __anext__(self):
        ...         ...

    ::

        >>> async for block in Lister(...):
        ...     ...

    ::

        >>> result = await Lister(...)
        >>> result
        [block, block, block, ...]
    """

    def __init__(self, *, timeout: Optional[float] = None) -> None:
        super().__init__()
        self.timeout = timeout

    @override
    def __aiter__(self) -> "AbstractAsyncLister[_T]":
        return self

    @with_timeout
    @abc.abstractmethod
    async def __anext__(self) -> _T:
        """
        :py:func:`asyncio.coroutine`

        Abstract method
        """


_T_acm = TypeVar("_T_acm", bound=AsyncContextManager[Any])


def async_enterable(
    f: Callable[_PS, Coroutine[None, None, _T_acm]],
) -> Callable[_PS, AsyncEnterableProtocol[_T_acm]]:
    """
    Decorator. Bring coroutine result up, so it can be used as async context

    ::

        >>> async def foo():
        ...
        ...     ...
        ...     return AsyncContextInstance(...)
        ...
        ... ctx = await foo()
        ... async with ctx:
        ...
        ...     # do

    ::

        >>> @async_enterable
        ... async def foo():
        ...
        ...     ...
        ...     return AsyncContextInstance(...)
        ...
        ... async with foo() as ctx:
        ...
        ...     # do
        ...
        ... ctx = await foo()
        ... async with ctx:
        ...
        ...     # do

    """

    @functools.wraps(f)
    def wrapper(*args: _PS.args, **kwargs: _PS.kwargs) -> AsyncEnterableProtocol[_T_acm]:
        class AsyncEnterableInstance(AsyncEnterableProtocol[_T_acm]):  # pyright: ignore[reportGeneralTypeIssues]
            @override
            async def __aenter__(self) -> _T_acm:
                self.context = await f(*args, **kwargs)
                return await self.context.__aenter__()  # type: ignore

            @override
            async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
                await self.context.__aexit__(*args, **kwargs)

            @override
            def __await__(self) -> Generator[None, None, _T_acm]:
                return f(*args, **kwargs).__await__()

        return AsyncEnterableInstance()

    return wrapper


_T_str_bounded = TypeVar("_T_str_bounded", bound=str)


@overload
def wrap_with_container(o: _T_str_bounded) -> Tuple[_T_str_bounded]: ...
@overload
def wrap_with_container(o: Any) -> Any: ...


def wrap_with_container(o: _T) -> Union[_T, Tuple[str]]:
    if isinstance(o, str):
        return (o,)
    return o


class StreamIO:
    """
    Stream input/output wrapper with timeout.

    :param reader: stream reader
    :type reader: :py:class:`asyncio.StreamReader`

    :param writer: stream writer
    :type writer: :py:class:`asyncio.StreamWriter`

    :param timeout: socket timeout for read/write operations
    :type timeout: :py:class:`int`, :py:class:`float` or :py:class:`None`

    :param read_timeout: socket timeout for read operations, overrides
        `timeout`
    :type read_timeout: :py:class:`int`, :py:class:`float` or :py:class:`None`

    :param write_timeout: socket timeout for write operations, overrides
        `timeout`
    :type write_timeout: :py:class:`int`, :py:class:`float` or :py:class:`None`
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        timeout: Optional[Union[int, float]] = None,
        read_timeout: Optional[Union[int, float]] = None,
        write_timeout: Optional[Union[int, float]] = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.read_timeout = read_timeout or timeout
        self.write_timeout = write_timeout or timeout

    @with_timeout("read_timeout")
    async def readline(self) -> bytes:
        """
        :py:func:`asyncio.coroutine`

        Proxy for :py:meth:`asyncio.StreamReader.readline`.
        """
        return await self.reader.readline()

    @with_timeout("read_timeout")
    async def read(self, count: int = -1) -> bytes:
        """
        :py:func:`asyncio.coroutine`

        Proxy for :py:meth:`asyncio.StreamReader.read`.

        :param count: block size for read operation
        :type count: :py:class:`int`
        """
        return await self.reader.read(count)

    @with_timeout("read_timeout")
    async def readexactly(self, count: int) -> bytes:
        """
        :py:func:`asyncio.coroutine`

        Proxy for :py:meth:`asyncio.StreamReader.readexactly`.

        :param count: block size for read operation
        :type count: :py:class:`int`
        """
        return await self.reader.readexactly(count)

    @with_timeout("write_timeout")
    async def write(self, data: bytes) -> None:
        """
        :py:func:`asyncio.coroutine`

        Combination of :py:meth:`asyncio.StreamWriter.write` and
        :py:meth:`asyncio.StreamWriter.drain`.

        :param data: data to write
        :type data: :py:class:`bytes`
        """
        self.writer.write(data)
        await self.writer.drain()

    def close(self) -> None:
        """
        Close connection.
        """
        self.writer.close()


class Throttle:
    """
    Throttle for streams.

    :param limit: speed limit in bytes or :py:class:`None` for unlimited
    :type limit: :py:class:`int` or :py:class:`None`

    :param reset_rate: time in seconds for «round» throttle memory (to deal
        with float precision when divide)
    :type reset_rate: :py:class:`int` or :py:class:`float`
    """

    def __init__(self, *, limit: Optional[int] = None, reset_rate: Union[int, float] = 10) -> None:
        self._limit = limit
        self.reset_rate = reset_rate
        self._start: Optional[float] = None
        self._sum = 0

    async def wait(self) -> None:
        """
        :py:func:`asyncio.coroutine`

        Wait until can do IO
        """
        if self._limit is not None and self._limit > 0 and self._start is not None:
            now = _now()
            end = self._start + self._sum / self._limit
            await asyncio.sleep(max(0, end - now))

    def append(self, data: bytes, start: float) -> None:
        """
        Count `data` for throttle

        :param data: bytes of data for count
        :type data: :py:class:`bytes`

        :param start: start of read/write time from
            :py:meth:`asyncio.BaseEventLoop.time`
        :type start: :py:class:`float`
        """
        if self._limit is not None and self._limit > 0:
            if self._start is None:
                self._start = start
            if start - self._start > self.reset_rate:
                self._sum -= round((start - self._start) * self._limit)
                self._start = start
            self._sum += len(data)

    @property
    def limit(self) -> Optional[int]:
        """
        Throttle limit
        """
        return self._limit

    @limit.setter
    def limit(self, value: Optional[int]) -> None:
        """
        Set throttle limit

        :param value: bytes per second
        :type value: :py:class:`int` or :py:class:`None`
        """
        self._limit = value
        self._start = None
        self._sum = 0

    def clone(self) -> "Throttle":
        """
        Clone throttle without memory
        """
        return Throttle(limit=self._limit, reset_rate=self.reset_rate)

    @override
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(limit={self._limit!r}, " f"reset_rate={self.reset_rate!r})"


class StreamThrottle(NamedTuple):
    """
    Stream throttle with `read` and `write` :py:class:`aioftp.Throttle`

    :param read: stream read throttle
    :type read: :py:class:`aioftp.Throttle`

    :param write: stream write throttle
    :type write: :py:class:`aioftp.Throttle`
    """

    read: Throttle
    write: Throttle

    def clone(self) -> "StreamThrottle":
        """
        Clone throttles without memory
        """
        return StreamThrottle(
            read=self.read.clone(),
            write=self.write.clone(),
        )

    @classmethod
    def from_limits(
        cls,
        read_speed_limit: Optional[int] = None,
        write_speed_limit: Optional[int] = None,
    ) -> "StreamThrottle":
        """
        Simple wrapper for creation :py:class:`aioftp.StreamThrottle`

        :param read_speed_limit: stream read speed limit in bytes or
            :py:class:`None` for unlimited
        :type read_speed_limit: :py:class:`int` or :py:class:`None`

        :param write_speed_limit: stream write speed limit in bytes or
            :py:class:`None` for unlimited
        :type write_speed_limit: :py:class:`int` or :py:class:`None`
        """
        return cls(
            read=Throttle(limit=read_speed_limit),
            write=Throttle(limit=write_speed_limit),
        )


class ThrottleStreamIO(StreamIO):
    """
    Throttled :py:class:`aioftp.StreamIO`. `ThrottleStreamIO` is subclass of
    :py:class:`aioftp.StreamIO`. `throttles` attribute is dictionary of `name`:
    :py:class:`aioftp.StreamThrottle` pairs

    :param *args: positional arguments for :py:class:`aioftp.StreamIO`
    :param **kwargs: keyword arguments for :py:class:`aioftp.StreamIO`

    :param throttles: dictionary of throttles
    :type throttles: :py:class:`dict` with :py:class:`aioftp.Throttle` values

    ::

        >>> self.stream = ThrottleStreamIO(
        ...     reader,
        ...     writer,
        ...     throttles={
        ...         "main": StreamThrottle(
        ...             read=Throttle(...),
        ...             write=Throttle(...)
        ...         )
        ...     },
        ...     timeout=timeout
        ... )
    """

    def __init__(self, *args: Any, throttles: Dict[str, StreamThrottle] = {}, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.throttles = throttles

    async def wait(self, name: str) -> None:
        """
        :py:func:`asyncio.coroutine`

        Wait for all throttles

        :param name: name of throttle to acquire ("read" or "write")
        :type name: :py:class:`str`
        """
        tasks: List[asyncio.Task[None]] = []
        for throttle in self.throttles.values():
            curr_throttle = getattr(throttle, name)
            if curr_throttle.limit:
                tasks.append(asyncio.create_task(curr_throttle.wait()))
        if tasks:
            await asyncio.wait(tasks)

    def append(self, name: str, data: bytes, start: float) -> None:
        """
        Update timeout for all throttles

        :param name: name of throttle to append to ("read" or "write")
        :type name: :py:class:`str`

        :param data: bytes of data for count
        :type data: :py:class:`bytes`

        :param start: start of read/write time from
            :py:meth:`asyncio.BaseEventLoop.time`
        :type start: :py:class:`float`
        """
        for throttle in self.throttles.values():
            getattr(throttle, name).append(data, start)

    @override
    async def read(self, count: int = -1) -> bytes:
        """
        :py:func:`asyncio.coroutine`

        :py:meth:`aioftp.StreamIO.read` proxy
        """
        await self.wait("read")
        start = _now()
        data = await super().read(count)
        self.append("read", data, start)
        return data

    @override
    async def readline(self) -> bytes:
        """
        :py:func:`asyncio.coroutine`

        :py:meth:`aioftp.StreamIO.readline` proxy
        """
        await self.wait("read")
        start = _now()
        data = await super().readline()
        self.append("read", data, start)
        return data

    @override
    async def write(self, data: bytes) -> None:
        """
        :py:func:`asyncio.coroutine`

        :py:meth:`aioftp.StreamIO.write` proxy
        """
        await self.wait("write")
        start = _now()
        await super().write(data)
        self.append("write", data, start)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def iter_by_line(self) -> AsyncStreamIterator[bytes]:
        """
        Read/iterate stream by line.

        :rtype: :py:class:`aioftp.AsyncStreamIterator`

        ::

            >>> async for line in stream.iter_by_line():
            ...     ...
        """
        return AsyncStreamIterator[bytes](self.readline)

    def iter_by_block(self, count: int = DEFAULT_BLOCK_SIZE) -> AsyncStreamIterator[bytes]:
        """
        Read/iterate stream by block.

        :rtype: :py:class:`aioftp.AsyncStreamIterator`

        ::

            >>> async for block in stream.iter_by_block(block_size):
            ...     ...
        """
        return AsyncStreamIterator(lambda: self.read(count))


LOCALE_LOCK = threading.Lock()


@contextmanager
def setlocale(name: str) -> Generator[str, None, None]:
    """
    Context manager with threading lock for set locale on enter, and set it
    back to original state on exit.

    ::

        >>> with setlocale("C"):
        ...     ...
    """
    with LOCALE_LOCK:
        old_locale = locale.setlocale(locale.LC_ALL)
        try:
            yield locale.setlocale(locale.LC_ALL, name)
        finally:
            locale.setlocale(locale.LC_ALL, old_locale)

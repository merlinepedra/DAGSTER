from contextlib import contextmanager
import datetime
from typing import Callable, Iterator, Optional
from typing_extensions import Final, Protocol, TypeAlias, runtime_checkable

import packaging.version
import pendulum

# We support both pendulum 1.x and 2.x. 2.x renamed some core functionality that we use. To make
# sure we maintain compatibility and to avoid choking type checkers (which lack a way to dynamically
# resolve a symbol depending on the environment), we use this module as a universal interface over
# `pendulum` 1 and 2.
# 
# It basically replicates the API of pendulum 2.x that we use, but checks if pendulum 1.x is
# installed and dispatches calls appropriately. Because we can't use Pendulum 2 objects in our type
# annotations, we instead use thin Protocols that stub the methods of pendulum classes that we use.

if packaging.version.parse(pendulum.__version__).major >= 2:
    PendulumDateTime: TypeAlias = pendulum.DateTime
else:
    PendulumDateTime: TypeAlias = pendulum.Pendulum

_IS_PENDULUM_2: Final[bool] = (
    hasattr(pendulum, "__version__")
    and getattr(packaging.version.parse(getattr(pendulum, "__version__")), "major")
    == 2
)

@runtime_checkable
class PendulumTimeZone(Protocol):

    @property
    def name(self) -> str:
        ...

@runtime_checkable
class PendulumDateTime(Protocol):
    
    def timestamp(self) -> float:
        ...

    @property
    def timezone(self) -> Optional[PendulumTimeZone]:
        ...


def now(tz: str) -> PendulumDateTime:
    return pendulum.now(tz)

def instance(dt: datetime.datetime, tz: Optional[str] = None) -> PendulumDateTime:
    return pendulum.instance(dt, tz=tz)


@contextmanager
def mock_pendulum_timezone(override_timezone: str) -> Iterator[None]:
    if _IS_PENDULUM_2:
        with pendulum.tz.test_local_timezone(  # type: ignore
             pendulum.tz.timezone(override_timezone)  # type: ignore
        ):

            yield
    else:
        with pendulum.tz.LocalTimezone.test(  # type: ignore
            pendulum.Timezone.load(override_timezone)  # type: ignore
        ):

            yield

def create_pendulum_time(year: int, month: int, day: int, *args: object, **kwargs: object) -> PendulumDateTime:
    fn_name = "create" if _IS_PENDULUM_2 else "datetime"
    return getattr(pendulum, fn_name)(year, month, day, *args, **kwargs)


# # pylint: disable=no-member
# PendulumDateTime = (
#     pendulum.DateTime if _IS_PENDULUM_2 else pendulum.Pendulum  # type: ignore[attr-defined]
# )

# Workaround for issues with .in_tz() in pendulum:
# https://github.com/sdispater/pendulum/issues/535
def to_timezone(dt: PendulumDateTime, tz: str) -> PendulumDateTime:
    import dagster._check as check

    check.inst_param(dt, "dt", PendulumDateTime)
    return pendulum.from_timestamp(dt.timestamp(), tz=tz)


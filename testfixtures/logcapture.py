from collections import defaultdict
import atexit
import logging
import warnings
from pprint import pformat

from testfixtures.comparison import compare
from testfixtures.utils import wrap


class LogCapture(logging.Handler):
    """
    These are used to capture entries logged to the Python logging
    framework and make assertions about what was logged.

    :param names: A string (or tuple of strings) containing the dotted name(s)
                  of loggers to capture. By default, the root logger is
                  captured.

    :param install: If `True`, the :class:`LogCapture` will be
                    installed as part of its instantiation.

    :param propagate: If specified, any captured loggers will have their
                      `propagate` attribute set to the supplied value. This can
                      be used to prevent propagation from a child logger to a
                      parent logger that has configured handlers.

    :param attributes:

      The sequence of attribute names to return for each record or a callable
      that extracts a row from a record.

      If a sequence of attribute names, those attributes will be taken from the
      :class:`~logging.LogRecord`. If an attribute is callable, the value
      used will be the result of calling it. If an attribute is missing,
      ``None`` will be used in its place.

      If a callable, it will be called with the :class:`~logging.LogRecord`
      and the value returned will be used as the row..

    :param recursive_check:

      If ``True``, log messages will be compared recursively by
      :meth:`LogCapture.check`.
    """

    instances = set()
    atexit_setup = False
    installed = False

    def __init__(self, names=None, install=True, level=1, propagate=None,
                 attributes=('name', 'levelname', 'getMessage'),
                 recursive_check=False):
        logging.Handler.__init__(self)
        if not isinstance(names, tuple):
            names = (names, )
        self.names = names
        self.level = level
        self.propagate = propagate
        self.attributes = attributes
        self.recursive_check = recursive_check
        self.old = defaultdict(dict)
        self.clear()
        if install:
            self.install()

    @classmethod
    def atexit(cls):
        if cls.instances:
            warnings.warn(
                'LogCapture instances not uninstalled by shutdown, '
                'loggers captured:\n'
                '%s' % ('\n'.join((str(i.names) for i in cls.instances)))
                )

    def clear(self):
        "Clear any entries that have been captured."
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def install(self):
        """
        Install this :class:`LogHandler` into the Python logging
        framework for the named loggers.

        This will remove any existing handlers for those loggers and
        drop their level to that specified on this :class:`LogCapture` in order
        to capture all logging.
        """
        for name in self.names:
            logger = logging.getLogger(name)
            self.old['levels'][name] = logger.level
            self.old['handlers'][name] = logger.handlers
            self.old['disabled'][name] = logger.disabled
            self.old['progagate'][name] = logger.propagate
            logger.setLevel(self.level)
            logger.handlers = [self]
            logger.disabled = False
            if self.propagate is not None:
                logger.propagate = self.propagate
        self.instances.add(self)
        if not self.__class__.atexit_setup:
            atexit.register(self.atexit)
            self.__class__.atexit_setup = True

    def uninstall(self):
        """
        Un-install this :class:`LogHandler` from the Python logging
        framework for the named loggers.

        This will re-instate any existing handlers for those loggers
        that were removed during installation and retore their level
        that prior to installation.
        """
        if self in self.instances:
            for name in self.names:
                logger = logging.getLogger(name)
                logger.setLevel(self.old['levels'][name])
                logger.handlers = self.old['handlers'][name]
                logger.disabled = self.old['disabled'][name]
                logger.propagate = self.old['progagate'][name]
            self.instances.remove(self)

    @classmethod
    def uninstall_all(cls):
        "This will uninstall all existing :class:`LogHandler` objects."
        for i in tuple(cls.instances):
            i.uninstall()

    def _actual_row(self, record):
        for a in self.attributes:
            value = getattr(record, a, None)
            if callable(value):
                value = value()
            yield value

    def actual(self):
        """
        The sequence of actual records logged, having had their attributes
        extracted as specified by the ``attributes`` parameter to the
        :class:`LogCapture` constructor.

        This can be useful for making more complex assertions about logged
        records. The actual records logged can also be inspected by using the
        :attr:`records` attribute.
        """
        actual = []
        for r in self.records:
            if callable(self.attributes):
                actual.append(self.attributes(r))
            else:
                result = tuple(self._actual_row(r))
                if len(result) == 1:
                    actual.append(result[0])
                else:
                    actual.append(result)
        return actual

    def __str__(self):
        if not self.records:
            return 'No logging captured'
        return '\n'.join(["%s %s\n  %s" % r for r in self.actual()])

    def check(self, *expected):
        """
        This will compare the captured entries with the expected
        entries provided and raise an :class:`AssertionError` if they
        do not match.

        :param expected:

          A sequence of entries of the structure specified by the ``attributes``
          passed to the constructor.
        """
        return compare(
            expected,
            actual=self.actual(),
            recursive=self.recursive_check
            )

    def check_present(self, *expected, **kw):
        """
        This will check if the captured entries contain all of the expected
        entries provided and raise an :class:`AssertionError` if not.
        This will ignore entries that have been captured but that do not
        match those in ``expected``.

        :param expected:

          A sequence of entries of the structure specified by the ``attributes``
          passed to the constructor.

        :param order_matters:

          A keyword-only parameter that controls whether the order of the
          captured entries is required to match those of the expected entries.
          Defaults to ``True``.
        """
        order_matters = kw.pop('order_matters', True)
        assert not kw, 'order_matters is the only keyword parameter'
        actual = self.actual()
        if order_matters:
            matched_indices = [0]
            matched = []
            for entry in expected:
                try:
                    index = actual.index(entry, matched_indices[-1])
                except ValueError:
                    if len(matched_indices) > 1:
                        matched_indices.pop()
                        matched.pop()
                    break
                else:
                    matched_indices.append(index+1)
                    matched.append(entry)
            else:
                return

            compare(expected,
                    actual=matched+actual[matched_indices[-1]:],
                    recursive=self.recursive_check)
        else:
            expected = list(expected)
            matched = []
            unmatched = []
            for entry in actual:
                try:
                    index = expected.index(entry)
                except ValueError:
                    unmatched.append(entry)
                else:
                    matched.append(expected.pop(index))
                if not expected:
                    break
            if expected:
                raise AssertionError((
                    'entries not as expected:\n\n'
                    'expected and found:\n%s\n\n'
                    'expected but not found:\n%s\n\n'
                    'other entries:\n%s'
                ) % (pformat(matched), pformat(expected), pformat(unmatched)))

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.uninstall()


class LogCaptureForDecorator(LogCapture):

    def install(self):
        LogCapture.install(self)
        self.clear()
        return self


def log_capture(*names, **kw):
    """
    A decorator for making a :class:`LogCapture` installed an
    available for the duration of a test function.

    :param names: An optional sequence of names specifying the loggers
                  to be captured. If not specified, the root logger
                  will be captured.

    Keyword parameters other than ``install`` may also be supplied and will be
    passed on to the :class:`LogCapture` constructor.
    """
    l = LogCaptureForDecorator(names or None, install=False, **kw)
    return wrap(l.install, l.uninstall)

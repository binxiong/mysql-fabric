"""Module containing classes and functions for working with the
persistent store.

The two main classes here are the :class:`MySQLPersister` and the
:class:`PersistentMeta`.

Initializing the persistence system
-----------------------------------

Before creating any persisters, it is necessary to set up the
persistance system using connection information where objects should
be persisted::

   import mysql.fabric.persistence as persistence

   persistence.init(host="localhost", port=3307,
                    user="fabric_user", password="xyzzy")


Thread Initialization
~~~~~~~~~~~~~~~~~~~~~

When starting a new thread, a persister have to be created.  Typical
code for initializing the persister system for a thread is::

   import mysql.fabric.persistence as persistence
   persistence.init_thread()

"""
import functools
import inspect
import logging
import threading
import uuid as _uuid

import mysql.fabric.server_utils as _server_utils
import mysql.fabric.errors as _errors

DEFAULT_DATABASE = 'fabric'

class PersistentMeta(type):
    """Metaclass for persistent classes.

    This is a metaclass for persistent classes. The metaclass is
    responsible for:

    - Keep track of the persister for the thread. There is exactly one
      persister for each thread and a thread is expected call the
      :meth:`init_thread` method with the persister when it starts.

    - Add the 'persister' attribute to each method, giving it the
      persister class assigned to the thread by the object store.

    - Registering the class with the object store so that it will get
      an init() and deinit() call when the state store inits and
      deinits.

    """

    thread_local = threading.local() # Thread-local store
    classes = []                     # List of all persistent classes

    @classmethod
    def init_thread(cls, persister):
        """Initialize thread-specific data.

        :param persister: Persister to use for the thread.
        """

        cls.thread_local.persister = persister

    @classmethod
    def deinit_thread(cls):
        """De-initialize thread-specific data.
        """

        cls.thread_local.persister = None

    @classmethod
    def wrapfunc(cls, func):
        """Wrap the function to pass the persister for the thread.

        The function is wrapped by adding the argument 'persister' if
        the function request it by having an explicit argument named
        'persister'.

        The wrapped function will set the 'persister' argument to the
        persister assigned to the thread, but if an explicit persister
        argument has been given in the call, it is passed.

        Note that the function is not wrapped if it has a keywords
        argument (** argument) even though it is possible to pass the
        persister to such a function.  The reason for this is that
        many function wrappers use keywords argument to pass the
        arguments to the underlying function.

        """

        # Check that the function can accept a 'persister' parameter
        # or accepts keywords.
        argspec = inspect.getargspec(func)
        if 'persister' not in argspec.args:
            return func         # No need to wrap it, so just return it

        original = func         # Create closure
        @functools.wraps(func)
        def _wrap(*args, **kwrds):
            # Check if an explicit persister were given to the call or
            # use the thread-assigned persister otherwise.
            if 'persister' not in kwrds or kwrds['persister'] is None:
                kwrds['persister'] = cls.thread_local.persister
            return original(*args, **kwrds)
        return _wrap


    def __new__(mcs, cname, cbases, cdict):
        """Create a new class from it's pieces.

        The method will construct a new class by wrapping each method
        of the class using a wrapper that adds the "persistor"
        attribute to the keyword arguments.

        Only callable attributes, class methods, static methods, and
        properties not starting with underscore will be wrapped. All
        other functions are left alone.

        """

        mcs.thread_local.persister = None

        for name, func in cdict.items():
            # Check for functions that should not be touched.

            if name.startswith('_'):
                continue        # Special or internal function

            # Wrap function differently depending on what it is.
            if callable(func):
                # Anything callable is wrapped.
                cdict[name] = mcs.wrapfunc(func)
            elif isinstance(func, (staticmethod, classmethod)):
                # Wrap the inner function of static and class methods
                # and re-wrap using the type constructor.
                cdict[name] = type(func)(mcs.wrapfunc(func.__get__(True)))
            elif isinstance(func, property) and func.fset is not None:
                # Properties are re-constructed from its parts and
                # wrap the fset, fget, and fdel functions.
                newfset = func.fset and mcs.wrapfunc(func.fset)
                newfget = func.fget and mcs.wrapfunc(func.fget)
                newfdel = func.fdel and mcs.wrapfunc(func.fdel)
                cdict[name] = property(newfget, newfset, newfdel, func.__doc__)
        return type.__new__(mcs, cname, cbases, cdict)

    def __init__(cls, cname, cbases, cdict):
        """Add the class to the list of classes that should be called
        on init.
        """
        PersistentMeta.classes.append(cls)

class Persistable(object):
    """Class for all persistable objects.
    """

    __metaclass__ = PersistentMeta

class MySQLPersister(object):
    """Class responsible for persisting objects to a MySQL database.

    The class is responsible for managing the connection with the
    database where the persistent objects are stored. There should
    normally be one instance of this class available for each thread.

    Before using the persister, the system have to be initialized
    using :method:`MySQLPersister.init`. This will provide connection
    information to the object database for each
    :class:`MySQLPersister` instance created, set up the object
    database, and give each subclass of :class:`Persistable` a chance
    to set itself up by calling the class init method.
    """
    # Information for connecting to the database
    connection_info = None

    @classmethod
    def init(cls, host, user, password=None, port=None, database=None,
             timeout=None):
        """Initialize the object persistance system.

        This function initializes the persistance system. The function
        is idempotent in the sense that it can be called multiple
        times to set up the system. The parameters provided are passed
        to the connector, so typically, these fields need to be
        present.

        :param host: Host name of the database server.
        :param port: Port of the database server. Defaults to 3306.
        :param user: User to use connecting to the database server.
        :param password: Password to use when connecting to the
                         database server.
        :param database: Database where the persistance information is
                         stored. Default is :const:`DEFAULT_DATABASE`.
        :param timeout: Timeout to connect to the database server.
        """
        if port is None:
            port = _server_utils.MYSQL_DEFAULT_PORT
        if database is None:
            database = DEFAULT_DATABASE

        # Save away the connection information, it will be used by the
        # threads.
        cls.connection_info = {
            "host": host, "port": port,
            "user": user, "password": password,
            "database": database,
            "connection_timeout" : timeout,
            }

    @classmethod
    def setup(cls):
        """Setup the object persistance system.

        Perform initialization, which in this case means creating
        the database if it does not exist.
        """
        info = cls.connection_info
        conn = _server_utils.create_mysql_connection(
            host=info["host"], port=info["port"],
            user=info["user"], password=info["password"],
            connection_timeout=info["connection_timeout"],
            autocommit=True, use_unicode=False
            )
        #TODO: Can 'database' be used for SQL injection?
        conn.cursor().execute(
            "CREATE DATABASE IF NOT EXISTS %s" %
            (cls.connection_info["database"], )
            )

    @classmethod
    def teardown(cls):
        """Tear down the object persistance system.

        This should only be called if the persistance database should
        be removed from the persistance server since it will delete
        all object tables.
        """
        info = cls.connection_info
        conn = _server_utils.create_mysql_connection(
            host=info['host'], port=info['port'],
            user=info['user'], password=info['password'],
            connection_timeout=info["connection_timeout"],
            autocommit=True, use_unicode=False)
        conn.cursor().execute(
            "DROP DATABASE IF EXISTS %s" % (info['database'],)
            )

    def __init__(self):
        """Constructor for MySQLPersister.
        """
        assert self.connection_info is not None
        info = self.connection_info
        self.__cnx = _server_utils.create_mysql_connection(
            host=info['host'], port=info['port'],
            user=info['user'], password=info['password'],
            connection_timeout=info["connection_timeout"],
            database=info["database"], autocommit=True, use_unicode=False)
        if self.uuid is None:
            _LOGGER.warning(
                "MySQLPersister does not support uuid or "
                "it is not configured."
                )

    def __del__(self):
        """Destructor for MySQLPersister.
        """
        try:
            if self.__cnx:
                _server_utils.destroy_mysql_connection(self.__cnx)
        except AttributeError:
            pass

    def begin(self):
        """Start a new transaction.
        """
        self.exec_stmt("BEGIN")

    def commit(self):
        """Commit an on-going transaction.
        """
        self.exec_stmt("COMMIT")

    def rollback(self):
        """Roll back an on-going transaction.
        """
        self.exec_stmt("ROLLBACK")

    @property
    def uuid(self):
        """Return the MySQLPersister's uuid if the server supports it.
        Otherwise, return None.
        """
        try:
            row = _server_utils.exec_mysql_stmt(self.__cnx,
                                                "SELECT @@GLOBAL.SERVER_UUID")
            return _uuid.UUID(row[0][0])
        except _errors.DatabaseError:
            pass

        return None

    def exec_stmt(self, stmt_str, options=None):
        """Execute statements against the server.

        See :meth:`mysql.fabric.server_utils.exec_stmt`.

        """

        while True:
            try:
                return _server_utils.exec_mysql_stmt(
                    self.__cnx, stmt_str, options
                    )
            except _errors.DatabaseError:
                if _server_utils.is_valid_mysql_connection(self.__cnx):
                    raise
                _server_utils.reestablish_mysql_connection(
                    self.__cnx, attempt=1, delay=0
                    )

def current_persister():
    """Return the persister for the current thread.
    """
    return PersistentMeta.thread_local.persister

def init_thread():
    """Initialize the persistence system for the thread.
    """
    PersistentMeta.init_thread(MySQLPersister())

def deinit_thread():
    """Initialize the persistence system for the thread.
    """
    PersistentMeta.deinit_thread()

_LOGGER = logging.getLogger(__name__)

def init(host, user, password=None, port=None, database=None, timeout=None):
    """Initialize the persistance system.

    This function is idempotent in the sense that it can be executed
    multiple times without destroying anything. This property is
    important since normally the :func:`setup` and :func:`teardown`
    are not idempotent.

    :param host: Hostname to connect to.
    :param user: User to connect as.
    :param password: Password to use when connecting. Default to the
                     empty password.
    :param port: Port to connect to. Default to 3306.
    :param database: Database to store object data in. Default to
                     :const:`DEFAULT_DATABASE`.
    """
    if database is None:
        database = DEFAULT_DATABASE

    _LOGGER.info("Initializing persister using user '%s' at server %s:%d "
                 "using database '%s'.", user, host, port, database)

    MySQLPersister.init(host=host, port=port,
                        user=user, password=password,
                        database=database, timeout=timeout)

def setup():
    """ Setup the persistance system globally.

    This means creating any databases, tables and constraints necessary in the
    persistence database.
    """
    MySQLPersister.setup()

    persister = MySQLPersister()
    for cls in PersistentMeta.classes:
        if hasattr(cls, 'create'):
            _LOGGER.debug("Create database objects for %s", cls.__name__)
            cls.create(persister=persister)

    #TODO : The constraints will not need to be created separately after this
    #TODO: After the sharded system is modified to boot the HA layer.
    #Initialize the constraints after creating the tables.
    for cls in PersistentMeta.classes:
        #Call the add_constraints method of those classes that sub-class from
        #Persistence and those which have an implementation of add_constraints.
        if hasattr(cls, 'add_constraints'):
            _LOGGER.debug("Create constraints for %s", cls.__name__)
            cls.add_constraints(persister=persister)


def teardown():
    """Teardown the persistance system globally.

    This means removing any tables,constraints created. Normally, this
    function does not have to be executed on shutdown since that would
    emove all necessary tables.
    """
    _LOGGER.info("Teardown persister.")

    persister = MySQLPersister()
    for cls in PersistentMeta.classes:
        #The constraints are dropped before dropping the tables.
        if hasattr(cls, 'drop_constraints'):
            #Call the drop_constraints method of those classes that
            #sub-class from Persistence and those which have an implementation
            #of drop_constraints.
            _LOGGER.debug("Drop constraints for %s", cls.__name__)
            cls.drop_constraints(persister=persister)

    for cls in PersistentMeta.classes:
        if hasattr(cls, 'drop'):
            _LOGGER.debug("Drop database objects for %s", cls.__name__)
            cls.drop(persister=persister)

    MySQLPersister.teardown()
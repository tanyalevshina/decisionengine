import enum
import importlib
import logging
import threading


class DataSpaceConfigurationError(Exception):
    """
    Errors related to database access
    """
    pass


class DataSpaceConnectionError(Exception):
    """
    Errors related to database access
    """
    pass


class DataSpaceError(Exception):
    """
    Errors related to database access
    """
    pass


class DataSpaceExistsError(Exception):
    """
    Errors related to database access
    """
    pass


class Singleton(type):
    """
    Singleton pattern using Metaclass
    http://stackoverflow.com/questions/6760685/creating-a-singleton-in-python
    """

    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args,
                                                   **kwargs)
        # Uncomment following to run __init__ everytime class is called
        # else:
        #     cls._instances[cls].__init__(*args, **kwargs)

        return cls._instances[cls]

class DataSourceLoader(object, metaclass=Singleton):

    _ds = None

    @staticmethod
    def create_datasource(module_name, class_name, config):
        ds = DataSourceLoader._ds
        if not ds:
            py_module = importlib.import_module(module_name)
            clazz = getattr(py_module, class_name)
            ds = clazz(config)
        return ds


class DataSpace():
    """
    DataSpace class is collection of datablocks and provides interface
    to the database used to store the actual data
    """

    #: Description of tables and their columns
    _tables_created = False

    def __init__(self, config):
        """
        :type config: :obj:`dict`
        :arg config: Configuration dictionary
        """

        self.logger = logging.getLogger()
        self.logger.debug('Initializing a dataspace')

        # Validate configuration
        if not config.get('dataspace'):
            self.logger.exception("Error in initializing DataSpace!")
            raise DataSpaceConfigurationError('Configuration is missing dataspace information: dataspace key not found.')
        elif not isinstance(config.get('dataspace'), dict):
            self.logger.exception("Error in initializing DataSpace!")
            raise DataSpaceConfigurationError('Invalid dataspace configuration: '
                                              'dataspace key must correspond to a dictionary')
        try:
            self._db_driver_name = config['dataspace']['datasource']['name']
            self._db_driver_module = config['dataspace']['datasource']['module']
            self._db_driver_config = config['dataspace']['datasource']['config']
        except KeyError:
            self.logger.exception("Error in initializing DataSpace!")
            raise DataSpaceConfigurationError('Invalid dataspace configuration')

        self.datasource = DataSourceLoader().create_datasource(self._db_driver_module,
                                                               self._db_driver_name,
                                                               self._db_driver_config)

        # Datablocks, current and previous, keyed by taskmanager_ids
        self.curr_datablocks = {}
        self.prev_datablocks = {}

        # Connect to the database
        try:
            self.datasource.connect()
        except DataSpaceConnectionError:
            self.logger.exception('Cannot connect to the datasource!')
            raise

        # Create tables if not created
        if not DataSpace._tables_created:
            try:
                self.datasource.create_tables()
                DataSpace._tables_created = True
            except Exception:
                self.logger.exception('Cannot create datebase tables')
                raise

    def __str__(self):
        return '%s' % vars(self)

    def insert(self, taskmanager_id, generation_id, key,
               value, header, metadata):
        try:
            self.datasource.insert(taskmanager_id, generation_id, key,
                                   value, header, metadata)
        except Exception:
            self.logger.exception("Error in dataspace insert!")
            raise

    def update(self, taskmanager_id, generation_id, key,
               value, header, metadata):
        try:
            self.datasource.update(taskmanager_id, generation_id, key,
                                   value, header, metadata)
        except Exception:
            self.logger.exception("Error in dataspace update!")
            raise

    def get_dataproduct(self, taskmanager_id, generation_id, key):
        return self.datasource.get_dataproduct(taskmanager_id, generation_id, key)

    def get_dataproducts(self, taskmanager_id):
        return self.datasource.get_dataproducts(taskmanager_id)

    def get_header(self, taskmanager_id, generation_id, key):
        return self.datasource.get_header(taskmanager_id, generation_id, key)

    def get_metadata(self, taskmanager_id, generation_id, key):
        return self.datasource.get_metadata(taskmanager_id, generation_id, key)

    def duplicate_datablock(self, taskmanager_id, generation_id,
                            new_generation_id):
        return self.datasource.duplicate_datablock(taskmanager_id, generation_id,
                                                   new_generation_id)

    def delete(self, taskmanager_id, all_generations=False):
        # Remove the latest generation of the datablock
        # If asked, remove all generations of the datablock
        pass

    def mark_expired(self, taskmanager_id, generation_id, key, expiry_time):
        pass

    def mark_demented(self, taskmanager_id, keys, generation_id=None):
        if not generation_id:
            generation_id = self.curr_datablocks[taskmanager_id].generation_id
        self.datasource.mark_demented(taskmanager_id, generation_id, keys)

    def close(self):
        self.datasource.close()

    def store_taskmanager(self, name, id):
        return self.datasource.store_taskmanager(name, id)

    def get_last_generation_id(self,
                               taskmanager_name,
                               taskmanager_id=None):
        return self.datasource.get_last_generation_id(taskmanager_name,
                                                      taskmanager_id)

    def get_taskmanager(self, taskmanager_name, taskmanager_id=None):
        return self.datasource.get_taskmanager(taskmanager_name, taskmanager_id)

    def get_taskmanagers(self, taskmanager_name=None, start_time=None, end_time=None):
        return self.datasource.get_taskmanagers(taskmanager_name, start_time, end_time)


MIN_RETENTION_INTERVAL_DAYS = 7
State = enum.Enum("State", "IDLE STARTING RUNNING SLEEPING STOPPING STOPPED ERROR")


class Reaper():
    """
    Reaper provides functionality of periodic deletion
    of data older than retention_interval in days
    """

    def __init__(self, config):
        """
        :type config: :obj:`dict`
        :arg config: Configuration dictionary
        """
        # Validate configuration
        self.logger = logging.getLogger()
        self.logger.debug('Initializing a reaper')

        if not config.get('dataspace'):
            self.logger.exception("Error in initializing Reaper!")
            raise DataSpaceConfigurationError('Configuration is missing dataspace information: dataspace key not found.')
        elif not isinstance(config.get('dataspace'), dict):
            self.logger.exception("Error in initializing Reaper!")
            raise DataSpaceConfigurationError('Invalid dataspace configuration: '
                                              'dataspace key must correspond to a dictionary')
        try:
            db_driver_name = config['dataspace']['datasource']['name']
            db_driver_module = config['dataspace']['datasource']['module']
            db_driver_config = config['dataspace']['datasource']['config']
            self.retention_interval = int(config['dataspace']['retention_interval_in_days'])
            if self.retention_interval < MIN_RETENTION_INTERVAL_DAYS:
                self.logger.exception("Error in initializing Reaper!")
                raise ValueError("For safety the data retention interval has to be greater than {} days".
                                 format(MIN_RETENTION_INTERVAL_DAYS))
        except KeyError:
            self.logger.exception("Error in initializing Reaper!")
            raise DataSpaceConfigurationError('Invalid dataspace configuration')

        self.datasource = DataSourceLoader().create_datasource(db_driver_module,
                                                               db_driver_name,
                                                               db_driver_config)

        self.stop_event = threading.Event()
        self.thread = None
        self.state = State.IDLE
        self.state_lock = threading.Lock()
        self.logger = logging.getLogger()
        self._cv = threading.Condition()

    def get_retention_interval(self):
        return self.retention_interval

    def set_retention_interval(self, interval):
        if int(interval) < MIN_RETENTION_INTERVAL_DAYS:
            self.logger.exception("Error in setting the retention interval")
            raise ValueError("For safety the data retention interval has to be greater than {} days".
                             format(MIN_RETENTION_INTERVAL_DAYS))
        self.retention_interval = interval

    def _set_state(self, value):
        with self.state_lock:
            if self.state != value:
                self.state = value

    def get_state(self):
        with self.state_lock:
            return self.state

    def __has_state_no_lock(self, this_state):
        '''
        During startup we check state, but we don't want to lock
        and prevent the thread from changing the state.
        That is the condition we are activly looking for!
        '''
        return self.state == this_state

    def reap(self):
        self.datasource.delete_data_older_than(self.retention_interval)

    def _reaper_loop(self, delay):
        '''
        The first thing this loop does should be to set the state
        to State.STARTING so the caller can validate the thread is
        in fact running and doing things.
        '''
        self._set_state(State.STARTING)

        if delay:
            self.logger.info(f"Reaper has a start delay of: {delay}.")
            self.stop_event.wait(delay)

        while not self.stop_event.is_set():
            try:
                self.logger.info("Reaper.reap() started.")
                self._set_state(State.RUNNING)
                self.reap()
            except Exception:
                self.logger.exception("Reaper.reap() failed.")
                self._set_state(State.ERROR)
                break
            self.logger.debug("Reaper waiting for time/stop_event.")
            self._set_state(State.SLEEPING)
            self.stop_event.wait(86400)
        else:
            self.logger.info("Reaper stopped")
            self._set_state(State.STOPPED)

    def start(self, delay=0):
        '''
        Start thread with an optional delay to start the thread in X seconds
        '''
        if (not self.thread or not self.thread.is_alive()) and not self.stop_event.is_set():
            try:
                self.thread = threading.Thread(group=None,
                                               target=self._reaper_loop,
                                               args=(delay, ),
                                               name="Reaper_loop_thread")

                # clear state if we are re-starting the thread
                self._set_state(State.IDLE)

                self.thread.start()
            except Exception:
                self.logger.exception('Reaper loop thread not started')

            # make sure the thread had a chance to actually start before returning
            # this should make sure the thread is running, ie that the scheduler picked it up
            with self._cv:
                self._cv.wait_for(lambda: not self.__has_state_no_lock(State.IDLE), timeout=3)

    def stop(self):
        if self.thread and self.thread.is_alive() and not self.stop_event.is_set():
            self.stop_event.set()
            self._set_state(State.STOPPING)
            try:
                self.thread.join()
            finally:
                self.thread = None
                self.stop_event.clear()

    def __repr__(self):
        return "Reaper, retention interval {}, state {}".format(self.retention_interval,
                                                                self.get_state())

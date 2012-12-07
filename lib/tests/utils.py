"""Module holding support utilities for tests.
"""
import uuid as _uuid

import mysql.hub.utils as _utils
import mysql.hub.server as _server
import mysql.hub.replication as _replication
import mysql.hub.executor as _executor

from mysql.hub.sharding import ShardMapping, RangeShardingSpecification

class SkipTests(type):
    """Metaclass which is used to skip test cases as follows::

      import unittest
      import tests.utils as _utils

      class TestCaseClass(unittest.TestCase):
        __metaclass__ = _utils.SkipTests
    """
    def __new__(cls, name, bases, dct):
        """Create a new instance for SkipTests.
        """
        for name, item in dct.items():
            if callable(item) and name.startswith("test"):
                dct[name] = None
        return type.__new__(cls, name, bases, dct)

class MySQLInstances(_utils.Singleton):
    """Contain a reference to the available set of MySQL Instances that can be
    used in a test case.
    """
    def __init__(self):
        self.__uris = []
        self.__instances = {}

    def add_uri(self, uri):
        assert(isinstance(uri, basestring))
        self.__uris.append(uri)

    def get_uri(self, number):
        return self.__uris[number]

    def get_instance(self, number):
        return self.__instances[number]

    def destroy_instances(self):
        for instance in self.__instances.values():
            _replication.stop_slave(instance, wait=True)
            _replication.reset_slave(instance, clean=True)
        self.__instances = {}

    def configure_instances(self, topology, user, passwd):
        persister = _executor.Executor().persister

        for number in topology.keys():
            master_uri = self.get_uri(number)

            master_uuid = _server.MySQLServer.discover_uuid(uri=master_uri,
                                                            user=user,
                                                            passwd=passwd)
            master = _server.MySQLServer(_uuid.UUID(master_uuid), master_uri,
                                         user, passwd)
            master.connect()
            _replication.stop_slave(master, wait=True)
            _replication.reset_master(master)
            _replication.reset_slave(master)
            master.read_only = False
            self.__instances[number] = master
            for slave_topology in topology[number]:
                slave = self.configure_instances(slave_topology, user, passwd)
                slave.read_only = True
                _replication.switch_master(slave, master, user, passwd)
                _replication.start_slave(slave, wait=True)
            return master

class ShardingUtils(object):

    @staticmethod
    def compare_shard_mapping(shard_mapping_1, shard_mapping_2):
        """Compare two sharding mappings with each other. Two sharding
        specifications are equal if they are defined on the same table, on
        the same column, are of the same type and use the same sharding
        specification.

        :param shard_mapping_1: shard mapping
        :param shard_mapping_2: shard mapping

        :return True if shard mappings are equal
                False if shard mappings are not equal
        """
        return isinstance(shard_mapping_1, ShardMapping) and \
                isinstance(shard_mapping_2, ShardMapping) and \
               shard_mapping_1.table_name == \
                        shard_mapping_2.table_name and \
                shard_mapping_1.column_name == \
                        shard_mapping_2.column_name and \
                shard_mapping_1.type_name == \
                            shard_mapping_2.type_name and \
                shard_mapping_1.sharding_specification == \
                            shard_mapping_2.sharding_specification
    @staticmethod
    def compare_range_specifications(range_specification_1,
                                         range_specification_2):
        """Compare two RANGE specification definitions. They are equal if they
        belong to the same sharding scheme, define the same upper and lower
        bound and map to the same server.

        :param range_specification_1: Range Sharding Specification
        :param range_specification_2: Range Sharding Specification

        :return True if Range Sharding Specifications are equal
                False if Range Sharding Specifications are not equal
        """
        return isinstance(range_specification_1, RangeShardingSpecification) and \
                isinstance(range_specification_2, RangeShardingSpecification) and \
                range_specification_1.name == \
                        range_specification_2.name and \
                range_specification_1.lower_bound == \
                        range_specification_2.lower_bound and \
                range_specification_1.upper_bound == \
                        range_specification_2.upper_bound and \
                range_specification_1.uuid == range_specification_2.uuid

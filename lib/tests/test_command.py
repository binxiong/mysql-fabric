import unittest
import sys

from cStringIO import StringIO

import mysql.hub.persistence as _persistence
import mysql.hub.command as _command
import mysql.hub.services as _services
import mysql.hub.protocols.xmlrpc as _xmlrpc
import mysql.hub.config as _config

import tests.utils

class NewCommand(_command.Command):
    command_options = [
        { 'options': [ '--daemonize'],
          'dest':  'daemonize',
          'default': False,
          'help': "Daemonize the manager"
          },
        ]

    def __init__(self):
        super(NewCommand, self).__init__()
        self.execution = None

    def add_options(self, parser):
        super(NewCommand, self).add_options(parser)
        self.execution = "added_option"

class NewErrorCommand(_command.Command):
    command_options = [
        { 'dest':  'daemonize',
          'default': False,
          'help': "Daemonize the manager"
          },
        ]

    def __init__(self):
        super(NewErrorCommand, self).__init__()
        self.execution = None

    def add_options(self, parser):
        super(NewErrorCommand, self).add_options(parser)
        self.execution = "added_option"

class NewRemoteCommand(_command.Command):
    group_name = "test"

    command_name = "remote_command"

    def __init__(self):
        super(NewRemoteCommand, self).__init__()
        self.execution = None

    def execute(self):
        self.execution = "executed"
        return self.execution

class NewErrorRemoteCommand(_command.Command):
    group_name = "test"

    command_name = "error_remote_command"

    def __init__(self):
        super(NewErrorRemoteCommand, self).__init__()
        self.execution = None

    def execute(self):
        self.execution = "executed"
        return self.execution

class TestCommand(unittest.TestCase):
    "Test command interface."

    def setUp(self):
        self.manager, self.proxy = tests.utils.setup_xmlrpc()
        _persistence.init_thread()

    def tearDown(self):
        _persistence.deinit_thread()
        tests.utils.teardown_xmlrpc(self.manager, self.proxy)

    def test_command(self):
        """Create a command and check its basic properties.
        """
        # Create command.
        from optparse import OptionParser
        cmd = NewCommand()

        # Setup client-side data.
        cmd.setup_client("Fake Client", None, None)
        self.assertEqual(cmd.client, "Fake Client")
        self.assertEqual(cmd.options, None)
        self.assertEqual(cmd.config, None)
        self.assertRaises(AssertionError, cmd.setup_server, None)

        # Setup server-side data.
        cmd.setup_client(None, None, None)
        cmd.setup_server("Fake Server")
        self.assertEqual(cmd.server, "Fake Server")
        self.assertRaises(
            AssertionError, cmd.setup_client, None, None, None
            )

        # Add options to possible command-line.
        cmd.add_options(OptionParser())
        self.assertEqual(cmd.execution, "added_option")

    def test_command_error(self):
        """Create a command that has malformed options.
        """
        # Create a command.
        from optparse import OptionParser
        cmd = NewErrorCommand()

        # Try to add malformed options.
        self.assertRaises(KeyError, cmd.add_options, OptionParser())
        self.assertEqual(cmd.execution, None)

        # Try to dispatch a request when client information was not
        # configured.
        self.assertRaises(AttributeError, cmd.dispatch)

    def test_remote_command(self):
        """Create a remote command and fire it.
        """
        # Create a remote command.
        remote_cmd = NewRemoteCommand()
        srv_manager = _services.ServiceManager()
        srv_manager.rpc_server.register_command(remote_cmd)

        # Configure a local command.
        from __main__ import xmlrpc_next_port
        from optparse import OptionParser
        params = {
            'protocol.xmlrpc': {
                'address': 'localhost:%d' % (xmlrpc_next_port, ),
                },
            }
        config = _config.Config(None, params, True)
        local_cmd = NewRemoteCommand()
        local_cmd.setup_client(_xmlrpc.MyClient(), None, config)

        # Dispatch request through local command to remote command.
        self.assertEqual(local_cmd.dispatch(), "Command :\n{ return = executed\n}")
        self.assertEqual(local_cmd.execution, None)
        self.assertEqual(remote_cmd.execution, "executed")

    def test_remote_command_error(self):
        """Try to execute unknown remote command.
        """
        # Configure a local command.
        from __main__ import xmlrpc_next_port
        from optparse import OptionParser
        params = {
            'protocol.xmlrpc': {
                'address': 'localhost:%d' % (xmlrpc_next_port, ),
                },
            }
        config = _config.Config(None, params, True)
        local_cmd = NewErrorRemoteCommand()
        local_cmd.setup_client(_xmlrpc.MyClient(), None, config)

        # Dispatch request through local command to unknown
        # remote command.
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        output = (
            "<Fault 1: \'<type \\\'exceptions.Exception\\\'>:method "
            "\"test.error_remote_command\" is not supported\'>\n"
            )
        self.assertEqual(local_cmd.dispatch(), "Command :\n{ return = None\n}")
        self.assertEqual(sys.stderr.getvalue(), output)
        sys.stderr = old_stderr

if __name__ == "__main__":
    unittest.main()

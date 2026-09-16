import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from zero import cli


class TestHealthCli(unittest.TestCase):
    def write(self,path,**changes):
        row={'heartbeat_at':100.0,'block':123,'block_lag':1,
             'consecutive_errors':0,'kill_state':False,'last_error':None}
        row.update(changes)
        with open(path,'w') as handle: json.dump(row,handle)

    def test_fresh_runtime_status_is_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'status.json'); self.write(path)
            args=SimpleNamespace(status=path,max_age=30.0)
            with patch('zero.cli.time.time',return_value=110.0), redirect_stdout(StringIO()):
                self.assertEqual(cli.cmd_health(args),0)

    def test_missing_stale_or_killed_status_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'status.json')
            args=SimpleNamespace(status=path,max_age=30.0)
            with redirect_stdout(StringIO()): self.assertEqual(cli.cmd_health(args),2)
            self.write(path)
            with patch('zero.cli.time.time',return_value=200.0), redirect_stdout(StringIO()):
                self.assertEqual(cli.cmd_health(args),2)
            self.write(path,kill_state=True)
            with patch('zero.cli.time.time',return_value=110.0), redirect_stdout(StringIO()):
                self.assertEqual(cli.cmd_health(args),3)

    def test_health_subcommand_exists(self):
        with patch('zero.cli.cmd_health',return_value=0) as health:
            self.assertEqual(cli.main(['health','--status','x','--max-age','60']),0)
        health.assert_called_once()


if __name__=='__main__': unittest.main()

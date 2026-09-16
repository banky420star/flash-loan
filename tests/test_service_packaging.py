from pathlib import Path
import unittest


class TestServicePackaging(unittest.TestCase):
    def test_service_launcher_is_unbuffered_and_has_no_signer_inputs(self):
        text=Path('scripts/run_service.sh').read_text()
        self.assertIn('python3 -u -m zero.cli swarm',text)
        self.assertIn('ZERO_SWARM_INTERVAL',text)
        lowered=text.lower()
        for forbidden in ('private_key','mnemonic','eth_sendrawtransaction'):
            self.assertNotIn(forbidden,lowered)

    def test_systemd_unit_is_restart_safe_and_hardened(self):
        text=Path('docker/zero.service').read_text()
        for marker in [
            'Restart=on-failure','RestartSec=5','WorkingDirectory=/opt/zero',
            'User=zero','NoNewPrivileges=true','PrivateTmp=true',
            'KillSignal=SIGTERM']:
            self.assertIn(marker,text)
        self.assertNotIn('PRIVATE_KEY',text)

    def test_dockerfile_runs_non_root(self):
        text=Path('docker/Dockerfile').read_text()
        self.assertIn('USER zero',text)
        self.assertIn('scripts/run_service.sh',text)
        self.assertNotIn('PRIVATE_KEY',text)


if __name__=='__main__': unittest.main()

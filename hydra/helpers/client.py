import json
import os
import stat
import subprocess
import time
from collections import OrderedDict
from datetime import datetime
from shutil import rmtree

import requests
import toml

from hydra.core.exc import HydraError
from hydra.core.version import get_version
from . import HydraHelper


class ClientHelper(HydraHelper):
    def pip_update_hydra(self):
        pip = self.config.get('client', 'pip_install') % self.config['hydra']
        self.app.log.info(f'Updating pip from remote {pip}')
        # Execvp will replace this process with the sidechain
        os.execvp('pip3', ['pip3', 'install', pip])

    def install_systemd(self, name, destination, user='ubuntu'):
        systemd = OrderedDict([
            ('Unit', OrderedDict([
                ('Description', f'{name} Loom Node'),
                ('After', 'network.target'),
            ])),
            ('Service', OrderedDict([
                ('Type', 'simple'),
                ('User', user),
                ('WorkingDirectory', destination),
                ('ExecStart', f'{destination}/start_blockchain.sh'),
                ('Restart', 'always'),
                ('RestartSec', 2),
                ('StartLimitInterval', 0),
                ('LimitNOFILE', 500000),
                ('StandardOutput', 'syslog'),
                ('StandardError', 'syslog'),
            ])),
            ('Install', OrderedDict([
                ('WantedBy', 'multi-user.target'),
            ])),
        ])

        service_name = f'{name}.service'
        self.app.log.info(f'Writing to {service_name}')

        with open(service_name, 'w+') as service_file:
            # SystemD is just terse TOML - CHANGE MY MIND
            service_file.write(toml.dumps(systemd).replace('"', '').replace(' = ', '='))

        systemd_service = f'/etc/systemd/system/{service_name}'
        self.app.log.info(f'Installing {systemd_service} as {user}')
        self.app.utils.binary_exec('sudo', 'cp', service_name, systemd_service)
        self.app.utils.binary_exec('sudo', 'chown', 'root:root', systemd_service)
        self.app.utils.binary_exec('sudo', 'systemctl', 'daemon-reload')
        self.app.utils.binary_exec('sudo', 'systemctl', 'enable', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'start', service_name)

    def uninstall_systemd(self, name):
        service_name = f'{name}.service'
        systemd_service = f'/etc/systemd/system/{service_name}'

        self.app.log.info(f'Uninstalling {service_name}')

        if not os.path.exists(systemd_service):
            raise HydraError(f'Systemd file {systemd_service} not found')

        self.app.utils.binary_exec('sudo', 'systemctl', 'stop', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'disable', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'reset-failed', service_name)
        self.app.utils.binary_exec('sudo', 'rm', systemd_service)
        self.app.utils.binary_exec('sudo', 'systemctl', 'daemon-reload')

    def find_and_kill_executable(self, destination):
        pid = self.app.client.get_pid(os.path.join(destination, 'shipchain'))
        if pid:
            self.app.log.info(f'Found matching executable running as PID {pid}')
            self.app.utils.binary_exec('sudo', 'kill', pid)
        else:
            self.app.log.info(f'No matching executable running.  Continuing.')

    def get_pid(self, executable_path):
        self.app.log.info(f'Scanning for running `shipchain` executables')

        pid_list = map(int, subprocess.check_output(['pidof', 'shipchain']).split())

        for pid in pid_list:
            try:
                pid_exe_path = subprocess.check_output(['realpath', f'/proc/{pid}/exe'])
                pid_exe_path = pid_exe_path.decode('utf-8').strip()

                if pid_exe_path == executable_path:
                    return str(pid)

            except Exception as exc:  # pylint: disable=broad-except
                self.app.log.warning(f'Unable to check executable path for PID {pid}. {exc}')

        return None

    def bootstrap(self, destination, version=None, destroy=False):
        if os.path.exists(destination):
            if not destroy:
                self.app.log.error(f'Node directory exists, use -D to delete: {destination}')
                return
            rmtree(destination)

        os.makedirs(destination)

        os.chdir(destination)

        self.app.utils.download_release_file('./shipchain', 'shipchain')

        os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)

        got_version = self.app.utils.binary_exec('./shipchain', 'version').stderr.strip()
        self.app.log.debug(f'Copied ShipChain binary version {got_version}')

        self.app.log.info('Initializing Loom...')

        self.app.utils.binary_exec('./shipchain', 'init')
        node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()

        time.sleep(1)  # Gotta wait a second because the priv_validator doesn't always show up

        validator = json.load(open('chaindata/config/priv_validator.json'))

        self.app.log.info('Your validator address is:')
        self.app.log.info(validator['address'])
        self.app.log.info('Your validator public key is:')
        self.app.log.info(validator['pub_key']['value'])
        self.app.log.info('Your node key is:')
        self.app.log.info(node_key)

        self.app.log.debug('Writing hydra metadata...')
        metadata = {
            'bootstrapped': datetime.utcnow().strftime('%c'),
            'address': validator['address'],
            'pubkey': validator['pub_key']['value'],
            'nodekey': node_key,
            'shipchain_version': version,
            'by': f'hydra-bootstrap-{get_version()}'
        }
        json.dump(metadata, open('.bootstrap.json', 'w+'), indent=2)

        self.app.log.info('Bootstrapped!')

    def configure(self, name, destination, **kwargs):
        version = kwargs['version'] if 'version' in kwargs else None
        peers = kwargs['peers'] if 'peers' in kwargs else None
        pex = kwargs['pex'] if 'pex' in kwargs else True
        address_book_strict = kwargs['address_book_strict'] if 'address_book_strict' in kwargs else False
        private_peers = kwargs['private_peers'] if 'private_peers' in kwargs else False

        self.app.log.debug(f'Version {version} requested')

        if not os.path.exists(destination):
            self.app.log.error(f'Configuring client at destination does not exist: {destination}')
            return

        if not peers:
            # Get the published peering data
            url = f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/hydra.json'
            try:
                remote_config = json.loads(requests.get(url).content)
            except Exception as exc:  # pylint: disable=broad-except
                self.app.log.warning(f'Error getting network details from {url}: {exc}')
                return
            peers = [(ip, validator['pubkey'], validator['nodekey'])
                     for ip, validator in remote_config['node_data'].items()]

        os.chdir(destination)
        self.app.log.info('Peers: ')
        for peer in peers:
            self.app.log.info(f'{peer}')

        # CHAINDATA/CONFIG/GENESIS.json
        self._copy_genesis(
            f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/chaindata/config/genesis.json',
            'chaindata/config/genesis.json'
        )

        # GENESIS.json
        self._copy_genesis(
            f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/genesis.json',
            'genesis.json'
        )

        # CONFIG.TOML
        self._configure_toml(pex, address_book_strict, peers, private_peers)

        # START_BLOCKCHAIN.sh
        self._create_startup_script(peers)

        self.app.log.info('Configured!')

    def _copy_genesis(self, url, file):
        self.app.log.info(f'Copying {url} to {file}')
        try:
            genesis = json.loads(requests.get(url).content)
        except Exception as exc:  # pylint: disable=broad-except
            self.app.log.warning(f'Error getting network details from {url}: {exc}')
            return

        json.dump(genesis, open(file, 'w+'), indent=4)

    def _configure_toml(self, pex, address_book_strict, peers, private_peers):

        self.app.log.info('Updating config.toml')
        with open('chaindata/config/config.toml', 'r') as config_toml:
            config = toml.load(config_toml, OrderedDict)

        config['p2p']['pex'] = pex
        self.app.log.info(f'Editing config.toml: p2p.pex = {config["p2p"]["pex"]}')

        config['p2p']['address_book_strict'] = address_book_strict
        self.app.log.info(f'Editing config.toml: p2p.address_book_strict = {config["p2p"]["address_book_strict"]}')

        config['p2p']['private_peer_ids'] = ','.join([nodekey for (ip, pub, nodekey) in peers]) if private_peers else ''
        self.app.log.info(f'Editing config.toml: p2p.private_peer_ids = {config["p2p"]["private_peer_ids"]}')

        config['proxy_app'] = 'tcp://0.0.0.0:46658'
        self.app.log.info(f'Editing config.toml: proxy_app = {config["proxy_app"]}')

        config['rpc']['laddr'] = 'tcp://0.0.0.0:46657'
        self.app.log.info(f'Editing config.toml: rpc.laddr = {config["rpc"]["laddr"]}')

        config['p2p']['laddr'] = 'tcp://0.0.0.0:46656'
        self.app.log.info(f'Editing config.toml: p2p.laddr = {config["p2p"]["laddr"]}')

        with open('chaindata/config/config.toml', 'w+') as config_toml:
            config_toml.write(toml.dumps(config))

    def _create_startup_script(self, peers):
        self.app.log.info('Creating start_blockchain.sh helper script')

        this_node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()

        persistent_peers = ','.join(
            [
                f'tcp://{nodekey}@{ip}:46656'
                for ip, pubkey, nodekey in peers
                if nodekey != this_node_key
            ])

        with open('start_blockchain.sh', 'w+') as start_script:
            start_script.write('#!/bin/bash\n\n')
            start_script.write('cd "${0%/*}/"\n')
            start_script.write(f'./shipchain run --persistent-peers {persistent_peers}\n')

        os.chmod('./start_blockchain.sh', os.stat('./start_blockchain.sh').st_mode | stat.S_IEXEC)

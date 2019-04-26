import base64
import json
import os
import stat
import subprocess
import time
from collections import OrderedDict
from datetime import datetime
from io import StringIO
from shutil import rmtree

import requests
import toml
import yaml

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

        try:
            pid_list = map(int, subprocess.check_output(['pidof', 'shipchain']).split())
        except subprocess.CalledProcessError:
            return None

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

        self.app.utils.download_release_file('./shipchain', 'shipchain', version)

        os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)

        got_version = self.app.utils.binary_exec('./shipchain', 'version').stderr.strip()
        self.app.log.debug(f'Copied ShipChain binary version {got_version}')

        # LOOM.YAML defaults for generating initial genesis.json
        loom_config = {
            'ChainID': 'default',
            'RegistryVersion': 2,
            'DPOSVersion': 2,
            'ReceiptsVersion': 2,
            'EVMAccountsEnabled': True,
            'TransferGateway': {
                'ContractEnabled': True
            },
            'LoomCoinTransferGateway': {
                'ContractEnabled': True
            },
            'ChainConfig': {
                'ContractEnabled': True
            },
        }
        open(f'loom.yaml', 'w+').write(
            yaml.dump(loom_config, indent=4, default_flow_style=False))

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

        hex_addr = self.app.utils.binary_exec('./shipchain', 'call', 'pubkey',
                                              validator['pub_key']['value']).stdout.strip()

        self.app.log.debug('Writing hydra metadata...')
        metadata = {
            'bootstrapped': datetime.utcnow().strftime('%c'),
            'address': validator['address'],
            'hex_address': f'0x{hex_addr[10:]}',
            'b64_address': base64.b64encode(bytes.fromhex(hex_addr[10:])).decode(),
            'pubkey': validator['pub_key']['value'],
            'nodekey': node_key,
            'shipchain_version': version,
            'by': f'hydra-bootstrap-{get_version()}'
        }
        json.dump(metadata, open('.bootstrap.json', 'w+'), indent=2)

        self.app.log.debug('Writing key files...')
        open('node_pub.key', 'w+').write(metadata['pubkey'])
        open('node_priv.key', 'w+').write(validator['priv_key']['value'])
        open('node_addr.b64', 'w+').write(metadata['b64_address'])

        self.app.log.info('Bootstrapped!')

    def _setup_oracle_loom_yaml(self):
        with open('loom.yaml', 'r+') as config_file:
            cfg = yaml.load(config_file)

        for gateway in ('TransferGateway', 'LoomCoinTransferGateway'):
            cfg[gateway]['OracleEnabled'] = True
            cfg[gateway]['EthereumURI'] = self.app.config['provision']['gateway']['ethereum_uri']

            cfg[gateway]['MainnetPrivateKeyPath'] = 'oracle_eth_priv.key'
            cfg[gateway]['MainnetPollInterval'] = self.app.config['provision']['gateway']['mainnet_poll_interval']

            cfg[gateway]['DAppChainPrivateKeyPath'] = 'node_priv.key'
            cfg[gateway]['DAppChainReadURI'] = 'http://localhost:46658/query'
            cfg[gateway]['DAppChainWriteURI'] = 'http://localhost:46658/rpc'
            cfg[gateway]['DAppChainEventsURI'] = 'ws://localhost:46658/queryws'
            cfg[gateway]['DAppChainPollInterval'] = self.app.config['provision']['gateway'][
                'dappchain_poll_interval']

            cfg[gateway]['OracleLogLevel'] = self.app.config['provision']['gateway']['oracle_log_level']
            cfg[gateway]['OracleLogDestination'] = f'file://{gateway}-oracle.log'
            cfg[gateway]['OracleStartupDelay'] = self.app.config['provision']['gateway']['oracle_startup_delay']
            cfg[gateway]['OracleReconnectInterval'] = self.app.config['provision']['gateway'][
                'oracle_reconnect_interval']

        cfg['TransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_tg_contract_hex_address']
        cfg['LoomCoinTransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_lctg_contract_hex_address']
        open('loom.yaml', 'w+').write(yaml.dump(cfg, indent=4))

    def configure(self, name, destination, **kwargs):
        peers = kwargs['peers'] if 'peers' in kwargs else None

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

        # LOOM.YAML
        self._copy_yaml(
            f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/loom.yaml',
            'loom.yaml'
        )
        if kwargs['oracle'] if 'oracle' in kwargs else False:
            self._setup_oracle_loom_yaml()

        # GENESIS.json
        self._copy_genesis(
            f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/genesis.json',
            'genesis.json'
        )

        # CONFIG.TOML
        self._configure_toml(kwargs['pex'] if 'pex' in kwargs else True,
                             kwargs['addr_book_strict'] if 'addr_book_strict' in kwargs else False,
                             peers,
                             kwargs['private_peers'] if 'private_peers' in kwargs else False)

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

    def _copy_yaml(self, url, file):
        self.app.log.info(f'Copying {url} to {file}')
        try:
            contents = yaml.load(StringIO(requests.get(url).text))
        except Exception as exc:  # pylint: disable=broad-except
            self.app.log.warning(f'Error getting yaml from {url}: {exc}')
            return

        open(file, 'w+').write(yaml.dump(contents, indent=4))

    def _fetch_my_ip(self):
        return self.app.utils.binary_exec('curl', '-4', 'https://ifconfig.co').stdout.strip()

    def _configure_toml(self, pex, addr_book_strict, peers, private_peers):

        self.app.log.info('Updating config.toml')
        with open('chaindata/config/config.toml', 'r') as config_toml:
            config = toml.load(config_toml, OrderedDict)

        config['p2p']['pex'] = pex
        self.app.log.info(f'Editing config.toml: p2p.pex = {config["p2p"]["pex"]}')

        config['p2p']['external_address'] = f'tcp://{self._fetch_my_ip()}:46656'
        self.app.log.info(f'Editing config.toml: p2p.external_address = {config["p2p"]["external_address"]}')

        config['p2p']['addr_book_strict'] = addr_book_strict
        self.app.log.info(f'Editing config.toml: p2p.addr_book_strict = {config["p2p"]["addr_book_strict"]}')

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

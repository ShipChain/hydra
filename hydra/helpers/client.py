import base64
import getpass
import json
import os
import socket
import stat
import subprocess
import tarfile
import time
from collections import OrderedDict
from datetime import datetime
from io import StringIO
from shutil import rmtree

import distro
import requests
import toml
import yaml
from requests.auth import HTTPBasicAuth
from tqdm import tqdm

from hydra.core.exc import HydraError
from hydra.core.version import get_version
import hydra.main
from . import HydraHelper


class ClientHelper(HydraHelper):
    def pip_update_hydra(self):
        pip = self.config.get('client', 'pip_install') % self.config['hydra']
        self.app.log.info(f'Updating pip from remote {pip}')
        # Execvp will replace this process with the sidechain
        os.execvp('pip3', ['pip3', 'install', '-U', '--user', pip])

    def install_systemd(self, name, destination, user='ubuntu', binary='shipchain'):
        systemd = OrderedDict([
            ('Unit', OrderedDict([
                ('Description', f'{name} Loom Node'),
                ('After', 'network.target'),
            ])),
            ('Service', OrderedDict([
                ('Type', 'simple'),
                ('User', user),
                ('WorkingDirectory', destination),
                ('ExecStart', f'{destination}/{"start_blockchain.sh" if binary is "shipchain" else binary}'),
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

        service_name = f'{name}{"" if binary is "shipchain" else f".{binary}"}.service'
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

    def uninstall_systemd(self, name, binary='shipchain'):
        service_name = f'{name}{"" if binary is "shipchain" else f".{binary}"}.service'
        systemd_service = f'/etc/systemd/system/{service_name}'

        self.app.log.info(f'Uninstalling {service_name}')

        if not os.path.exists(systemd_service):
            raise HydraError(f'Systemd file {systemd_service} not found')

        self.app.utils.binary_exec('sudo', 'systemctl', 'stop', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'disable', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'reset-failed', service_name)
        self.app.utils.binary_exec('sudo', 'systemctl', 'daemon-reload')

    def find_and_kill_executable(self, destination, binary='shipchain'):
        pid = self.app.client.get_pid(os.path.join(destination, binary), binary)
        if pid:
            self.app.log.info(f'Found matching executable running as PID {pid}')
            self.app.utils.binary_exec('sudo', 'kill', pid)
        else:
            self.app.log.info(f'No matching executable running.  Continuing.')

    def stop_service(self, name, destination, binary='shipchain'):
        service_name = f'{name}{"" if binary is "shipchain" else f".{binary}"}.service'
        systemd_service = f'/etc/systemd/system/{service_name}'

        if os.path.exists(systemd_service):
            command = ['sudo', 'systemctl', 'stop', f'{name}{"" if binary is "shipchain" else f".{binary}"}']
            self.app.log.info(' '.join(command))
            self.app.utils.binary_exec(*command)

            time.sleep(1)

            command = ['sudo', 'systemctl', 'kill', f'{name}{"" if binary is "shipchain" else f".{binary}"}']
            self.app.log.info(' '.join(command))
            self.app.utils.binary_exec(*command)
        else:
            self.app.log.info(f'Service not installed.  Attempting to stop executable.')
            self.app.client.find_and_kill_executable(destination, binary)

    def start_service(self, name, binary='shipchain'):
        service_name = f'{name}{"" if binary is "shipchain" else f".{binary}"}.service'
        systemd_service = f'/etc/systemd/system/{service_name}'

        if os.path.exists(systemd_service):
            command = ['sudo', 'systemctl', 'start', f'{name}{"" if binary is "shipchain" else f".{binary}"}']
            self.app.log.info(' '.join(command))
            self.app.utils.binary_exec(*command)
        else:
            self.app.log.warning(f'Service not installed.  You will need to restart your node manually.')

    def get_pid(self, executable_path, binary='shipchain'):
        self.app.log.info(f'Scanning for running `{binary}` executables')

        try:
            pid_list = map(int, subprocess.check_output(['pidof', binary]).split())
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

    def bootstrap(self, destination, version=None, destroy=False, oracle=False):
        if os.path.exists(destination):
            if not destroy:
                self.app.log.error(f'Node directory exists, use -D to delete: {destination}')
                return
            rmtree(destination)

        os.makedirs(destination)

        os.chdir(destination)

        self.app.utils.download_release_file('./shipchain', 'shipchain', version)
        os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)

        if oracle:
            self.app.utils.download_release_file('./tgoracle', 'tgoracle', version)
            self.app.utils.download_release_file('./loomcoin_tgoracle', 'loomcoin_tgoracle', version)
            os.chmod('./tgoracle', os.stat('./tgoracle').st_mode | stat.S_IEXEC)
            os.chmod('./loomcoin_tgoracle', os.stat('./loomcoin_tgoracle').st_mode | stat.S_IEXEC)

        got_version = self.app.utils.binary_exec('./shipchain', 'version').stderr.strip()
        self.app.log.debug(f'Copied ShipChain binary version {got_version}')

        # LOOM.YAML defaults for generating initial genesis.json
        loom_config = {
            'ChainID': self.app.config['provision'].get('chain_id', hydra.main.CONFIG['provision']['chain_id']),
            'RegistryVersion': 2,
            'DPOSVersion': 3,
            'ReceiptsVersion': 2,
            'EVMAccountsEnabled': True,
            'TransferGateway': {
                'ContractEnabled': True
            },
            'LoomCoinTransferGateway': {
                'ContractEnabled': True
            },
            'BinanceTransferGateway': {
                'ContractEnabled': False
            },
            'ChainConfig': {
                'ContractEnabled': True
            },
            'DBBackend': 'cleveldb',
        }
        open(f'loom.yaml', 'w+').write(
            yaml.dump(loom_config, indent=4, default_flow_style=False))

        self.app.log.info('Initializing Loom...')

        self.app.utils.binary_exec('./shipchain', 'init')

        # Gotta wait a second because the priv_validator doesn't always show up immediately after the init
        time.sleep(1)

        self.update_node_helper_files(version)

        self.app.log.info('Bootstrapped!')

    def update_node_helper_files(self, version):
        node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()
        validator = json.load(open('chaindata/config/priv_validator.json'))

        self.app.log.info('Your validator address is:')
        self.app.log.info(validator['address'])
        self.app.log.info('Your validator public key is:')
        self.app.log.info(validator['pub_key']['value'])
        self.app.log.info('Your node key is:')
        self.app.log.info(node_key)

        self.app.log.debug('Writing hydra metadata...')
        hex_addr = self.app.utils.binary_exec('./shipchain', 'call', 'pubkey',
                                              validator['pub_key']['value']).stdout.strip()
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

        open('node_priv.key', 'w+').write(validator['priv_key']['value'])

    def jumpstart(self, name, network_directory, block):
        self.app.log.info(f'Attempting to jumpstart {name} to block: {block}.')

        url = f'{self.app.config["hydra"]["channel_url"]}/jumpstart/{name}/jumps.json'

        # Get the published jumpstart data
        try:
            jumps_json = json.loads(requests.get(url).content)
        except Exception as exc:  # pylint: disable=broad-except
            self.app.log.debug(f'Jumpstart metadata retrieval failed with: {exc}')
            self.app.log.warning(f'No jumpstart data found for network {name}.  Continuing without jumpstart')
            return

        if block not in jumps_json:
            raise HydraError(f'Network {name} does not have jumpstart for block {block}')

        # Next operations will all occur within the node directory for this network
        os.chdir(network_directory)

        # Get the jumpstart gzipped tar file
        url = f'{self.app.config["hydra"]["channel_url"]}/jumpstart/{name}/{jumps_json[block]}'
        jumpstart_tarfile = jumps_json[block]

        try:
            self.app.utils.download_file_stream(jumpstart_tarfile, url)
        except Exception as exc:
            raise HydraError(f'Unable to download jumpstart file {jumpstart_tarfile}: {exc}')

        # Cleanup existing data that we're overwriting from jumpstart
        for delete_dir in ['app.db', 'receipts_db', 'chaindata/data']:
            try:
                rmtree(os.path.join(network_directory, delete_dir))
            except FileNotFoundError as exc:
                self.app.log.debug(f'{exc}')
            except IOError as exc:
                raise HydraError(f'Cleanup of existing data failed: {exc}')

        # Extract jumpstart over network directory
        self.app.log.info(f'Extracting jumpstart contents')
        try:
            with tarfile.open(jumpstart_tarfile) as tar:
                tar_members = tar.getmembers()
                for member in tqdm(iterable=tar_members, total=(len(tar_members) - 1)):
                    tar.extract(member=member)
        except tarfile.TarError as exc:
            raise HydraError(f'Unable to extract jumpstart file {jumpstart_tarfile}: {exc}')

        # Cleanup jumpstart gzipped tar file
        try:
            os.remove(jumpstart_tarfile)
        except OSError as exc:
            self.app.log.warning(f'Unable to cleanup jumpstart tar. {exc}')

        self.app.log.info(f'Jumpstarting network complete!')

    def _setup_oracle_loom_yaml(self):
        with open('loom.yaml', 'r+') as config_file:
            cfg = yaml.load(config_file)

        for gateway in ('TransferGateway', 'LoomCoinTransferGateway'):
            cfg[gateway]['ContractEnabled'] = True
            cfg[gateway]['OracleEnabled'] = False
            cfg[gateway]['EthereumURI'] = self.app.config['provision']['gateway']['ethereum_uri']

            cfg[gateway]['MainnetPrivateKeyPath'] = 'oracle_eth_priv.key'
            cfg[gateway]['MainnetPollInterval'] = self.app.config['provision']['gateway'][
                'mainnet_poll_interval']

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

            cfg[gateway]['WithdrawalSig'] = 2

        cfg['TransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_tg_contract_hex_address']
        cfg['LoomCoinTransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_lctg_contract_hex_address']
        open('loom.yaml', 'w+').write(yaml.dump(cfg, indent=4))

    def configure(self, name, destination, **kwargs):
        peers = kwargs['peers'] if 'peers' in kwargs else None
        version = kwargs['version'] if 'version' in kwargs else None

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

        # Update bootstrap files
        self.update_node_helper_files(version)

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

        self.configure_metrics()

        self.app.log.info('Configured!')

    def update_validator_registry(self, info):
        node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()
        validator = json.load(open('chaindata/config/priv_validator.json'))
        hex_addr = self.app.utils.binary_exec('./shipchain', 'call', 'pubkey',
                                              validator['pub_key']['value']).stdout.strip()

        # Upserts the validator's info in the ShipChain validator registry
        self.app.log.info('Updating ShipChain validator registry')
        params = {
            'node_name': info['node_name'],
            'description': info['description'],
            'website': info['website'],
            'email': info['email'],
            'primary_contact': info['primary_contact'],
            'node_key': node_key,
            'public_key': validator['pub_key']['value'],
            'loom_address_hex': f'0x{hex_addr[10:]}',
            'loom_address_b64': base64.b64encode(bytes.fromhex(hex_addr[10:])).decode()
        }
        response = requests.post('https://registry.network.shipchain.io/validators/',
                                 json=params)
        response_json = response.json()

        if response.status_code == 400:
            registry_auth = None
            if 'email' in response_json and 'user exists' in response_json['email'][0]:
                # User already exists, requests require auth
                password = getpass.getpass('Enter your password to the ShipChain validator registry:')
                registry_auth = HTTPBasicAuth(params['email'], password)

            if 'node_key' in response_json and 'already exists' in response_json['node_key'][0]:
                # Node exists, update instead of create
                response = requests.put(f'https://registry.network.shipchain.io/validators/{params["node_key"]}',
                                        json=params, auth=registry_auth)
                if response.status_code != 200:
                    # TODO: handle error updating
                    self.app.log.error(f'Error from registry: {response.content}')
                    return None
            else:
                response = requests.post('https://registry.network.shipchain.io/validators/',
                                         json=params, auth=registry_auth)
                if response.status_code != 201:
                    # TODO: handle error creating
                    self.app.log.error(f'Error from registry: {response.content}')
                    return None
                else:
                    self.app.log.info(f'Successfully created user for {params["email"]}. '
                                      f'Expect a password reset email to arrive shortly.')

            response_json = response.json()

        return response_json

    def configure_metrics(self):
        self.app.log.info('Updating config.toml')
        with open('chaindata/config/config.toml', 'r') as config_toml:
            config = toml.load(config_toml, OrderedDict)

        config['instrumentation']['prometheus'] = 'true' if self.app.config['hydra'].getboolean('validator_metrics') else 'false'
        self.app.log.info(f'Editing config.toml: p2p.laddr = {config["instrumentation"]["prometheus"]}')

        with open('chaindata/config/config.toml', 'w+') as config_toml:
            config_toml.write(toml.dumps(config))

        if self.app.utils.config['hydra'].getboolean('validator_metrics'):
            try:
                validator_info = json.load(open('.validator-info.json', 'r'))
                self._configure_rsyslog()
                self._install_telegraf()
                self._configure_telegraf(validator_info['node_key'], validator_info['influxdb_pass'])
            except FileNotFoundError:
                self.app.log.error("Validator info not set, please run 'hydra client set-info' "
                                   "before attempting to configure metrics")
                return

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

        config['p2p']['send_rate'] = 20000000
        self.app.log.info(f'Editing config.toml: p2p.send_rate = {config["p2p"]["send_rate"]}')

        config['p2p']['recv_rate'] = 20000000
        self.app.log.info(f'Editing config.toml: p2p.recv_rate = {config["p2p"]["recv_rate"]}')

        config['p2p']['flush_throttle_timeout'] = "10ms"
        self.app.log.info(f'Editing config.toml: p2p.flush_throttle_timeout = {config["p2p"]["flush_throttle_timeout"]}')

        config['p2p']['max_packet_msg_payload_size'] = 10240
        self.app.log.info(f'Editing config.toml: p2p.max_packet_msg_payload_size = {config["p2p"]["max_packet_msg_payload_size"]}')

        config['proxy_app'] = 'tcp://0.0.0.0:46658'
        self.app.log.info(f'Editing config.toml: proxy_app = {config["proxy_app"]}')

        config['rpc']['laddr'] = 'tcp://0.0.0.0:46657'
        self.app.log.info(f'Editing config.toml: rpc.laddr = {config["rpc"]["laddr"]}')

        config['p2p']['laddr'] = 'tcp://0.0.0.0:46656'
        self.app.log.info(f'Editing config.toml: p2p.laddr = {config["p2p"]["laddr"]}')

        config['recheck'] = False
        self.app.log.info(f'Editing config.toml: recheck = {config["recheck"]}')

        config['db_backend'] = 'cleveldb'
        self.app.log.info(f'Editing config.toml: db_backend = {config["db_backend"]}')

        with open('chaindata/config/config.toml', 'w+') as config_toml:
            config_toml.write(toml.dumps(config))

    def _configure_rsyslog(self):
        # This rsyslog config file filters system logs for messages related to ShipChain, and exposes them for telegraf
        self.app.log.info('Configuring system log reporting')
        config = """
$ActionQueueType LinkedList # use asynchronous processing
$ActionQueueFileName srvrfwd # set file name, also enables disk mode
$ActionResumeRetryCount -1 # infinite retries on insert failure
$ActionQueueSaveOnShutdown on # save in-memory data if rsyslog shuts down
if $msg contains "shipchain" or $programname == "start_blockchain.sh" then @@(o)127.0.0.1:6514;RSYSLOG_SyslogProtocol23Format
"""
        with open('/tmp/50-telegraf.conf', 'w+') as conf:
            conf.write(config)
        self.app.utils.binary_exec('sudo', 'mv', '/tmp/50-telegraf.conf', '/etc/rsyslog.d/50-telegraf.conf')
        # service rsyslog restart
        self.app.utils.binary_exec('sudo', 'systemctl', 'restart', 'rsyslog')

    def _install_telegraf(self):
        self.app.log.info('Installing telegraf for metrics reporting')

        lsb = distro.lsb_release_info()
        if lsb['distributor_id'].lower() == 'ubuntu':
            self.app.utils.binary_exec('sudo', 'apt-key', 'adv', '--fetch-keys', 'https://repos.influxdata.com/influxdb.key')

            with open('/tmp/influxdb.list', 'w+') as influxdb_list:
                influxdb_list.write(f"deb https://repos.influxdata.com/{lsb['distributor_id'].lower()} {lsb['codename']} stable")
            self.app.utils.binary_exec('sudo', 'mv', '/tmp/influxdb.list', '/etc/apt/sources.list.d/influxdb.list')

            self.app.utils.binary_exec('sudo', 'apt-get', 'update')
            self.app.utils.binary_exec('sudo', 'apt-get', 'install', '-y', 'telegraf')
            self.app.utils.binary_exec('sudo', 'systemctl', 'enable', 'telegraf')

        else:
            # print warning and link to telegraf install
            self.app.log.warning(f'Automated telegraf installation not supported for {lsb["distributor_id"]}. '
                                 f'Please see manual installation instructions: '
                                 f'https://docs.influxdata.com/telegraf/v1.10/introduction/installation/')

    def _configure_telegraf(self, influxdb_username, influxdb_password):
        self.app.log.info('Updating telegraf config')
        with open('chaindata/config/config.toml', 'r') as config_toml:
            tendermint_config = toml.load(config_toml, OrderedDict)
        if 'moniker' in tendermint_config and tendermint_config['moniker']:
            moniker = tendermint_config['moniker']
        else:  # Default to hostname
            moniker = socket.gethostname()

        with open('/etc/telegraf/telegraf.conf', 'r') as config_toml:
            config = toml.load(config_toml, OrderedDict)

        config['global_tags']['moniker'] = moniker
        config['agent']['flush_jitter'] = '5s'  # Avoid thundering herd to influxdb
        config['outputs']['influxdb'] = [
            {
                'urls': ['https://metrics.network.shipchain.io:8086'],
                'skip_database_creation': True,
                'username': influxdb_username,
                'password': influxdb_password,
                'tagexclude': ['url'],
            }
        ]
        config['inputs']['syslog'] = [
            {
                'server': "tcp://:6514"
            }
        ]
        config['inputs']['prometheus'] = [
            {
                'urls': ['http://localhost:46658/metrics']
            }
        ]
        config['inputs']['net'] = [
            {
                'ignore_protocol_stats': True
            }
        ]

        with open('/tmp/telegraf.conf', 'w+') as config_toml:
            config_toml.write(toml.dumps(config))
        self.app.utils.binary_exec('sudo', 'mv', '/tmp/telegraf.conf', '/etc/telegraf/telegraf.conf')
        self.app.utils.binary_exec('sudo', 'systemctl', 'restart', 'telegraf')

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
            args = f' --persistent-peers {persistent_peers}' if persistent_peers else ''
            start_script.write(f'./shipchain run{args}\n')

        os.chmod('./start_blockchain.sh', os.stat('./start_blockchain.sh').st_mode | stat.S_IEXEC)

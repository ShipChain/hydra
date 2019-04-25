import json
import os
from collections import OrderedDict
from shutil import rmtree

import toml
import requests
import yaml
import sys
from cement import Controller, ex

from hydra.core.exc import HydraError


class Client(Controller):  # pylint: disable=too-many-ancestors
    class Meta:
        label = 'client'
        stacked_on = 'base'
        stacked_type = 'nested'
        # text displayed at the top of --help output
        description = 'Client tools for connecting to remote networks'

    def client_exec(self, *args):
        os.chdir(self.app.client.path())
        self.app.log.debug('Running: ./shipchain ' + ' '.join(args))
        return self.app.client.exec(*args)

    @ex()
    def update(self):
        self.app.client.pip_update_hydra()

    @ex(
        arguments=[
            (
                ['-u', '--url'],
                {
                    'help': '',
                    'action': 'store_true',
                    'dest': 'url'
                }
            ),
        ]
    )
    def set_channel(self):
        if not self.app.pargs.url:
            self.app.log.error('You must specify a --url')
            return

        with open(self.app.config_file, 'r+') as config_file:
            cfg = yaml.load(config_file)

        cfg['hydra']['channel_url'] = self.app.pargs.url

        open(self.app.config_file, 'w+').write(yaml.dump(cfg, indent=4))

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to join',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-D', '--destroy'],
                {
                    'help': 'destroy existing directory',
                    'action': 'store_true',
                    'dest': 'destroy'
                }
            ),
            (
                ['-v', '--version'],
                {
                    'help': 'version of network software to run',
                    'action': 'store',
                    'dest': 'version'
                }
            ),
            (
                ['-d', '--destination'],
                {
                    'help': 'destination directory',
                    'action': 'store',
                    'dest': 'destination'
                }
            ),
            (
                ['--set-default'],
                {
                    'help': 'save as default in .hydra_network',
                    'action': 'store_true',
                    'dest': 'default'
                }
            ),
            (
                ['--install'],
                {
                    'help': 'install systemd service for the network',
                    'action': 'store_true',
                    'dest': 'install'
                }
            ),
            (
                ['--no-configure'],
                {
                    'help': 'prevent the configuration step from running',
                    'action': 'store_false',
                    'dest': 'do_configure',
                    'default': 'true'
                }
            ),
        ]
    )
    def join_network(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)

        if self.app.pargs.default:
            with open(self.app.utils.path('.hydra_network'), 'w+') as network_file:
                network_file.write(name)

        if os.path.exists(destination):
            if not self.app.pargs.destroy:
                self.app.log.error(
                    f'Node directory exists, use -D to delete: {destination}')
                return
            rmtree(destination)

        if not self.app.pargs.version:
            url = f'{self.app.config["hydra"]["channel_url"]}/networks/{name}/hydra.json'
            try:
                remote_config = json.loads(requests.get(url).content)
                version = remote_config['version']
            except json.JSONDecodeError:
                self.app.log.warning(
                    f'Error getting network version details from {url}, using "latest"')
                version = "latest"
        else:
            version = self.app.pargs.version

        self.app.client.bootstrap(
            destination, version=version, destroy=self.app.pargs.destroy)

        if self.app.pargs.do_configure:
            self.app.client.configure(name, destination)

        if self.app.pargs.install:
            self.app.client.install_systemd(
                name, destination, user=self.app.utils.binary_exec('whoami').stdout.strip())

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to join',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-v', '--version'],
                {
                    'help': 'version of network software to run',
                    'action': 'store',
                    'dest': 'version'
                }
            ),
            (
                ['-d', '--destination'],
                {
                    'help': 'destination directory',
                    'action': 'store',
                    'dest': 'destination'
                }
            ),
            (
                ['--install'],
                {
                    'help': 'install systemd service for the network',
                    'action': 'store_true',
                    'dest': 'install'
                }
            ),
            (
                ['--peer'],
                {
                    'help': 'set the peer',
                    'action': 'store_true',
                    'dest': 'install'
                }
            ),
            (
                ['--as-oracle'],
                {
                    'help': 'join the network as the transfer-gateway oracle',
                    'action': 'store_true',
                    'dest': 'oracle'
                }
            ),
        ]
    )
    def configure(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)

        if not os.path.exists(destination):
            self.app.log.error(f'Directory doesnt exist: {destination}')

        self.app.client.configure(
            name, destination, version=self.app.pargs.version or 'latest', oracle=self.app.pargs.oracle)

        if self.app.pargs.install:
            self.app.client.install_systemd(
                name, destination, user=self.app.utils.binary_exec('whoami').stdout.strip())

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to leave',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-d', '--destination'],
                {
                    'help': 'destination directory',
                    'action': 'store',
                    'dest': 'destination'
                }
            ),
        ]
    )
    def leave_network(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)

        # Verify network directory exists before we start removing everything
        if not os.path.exists(destination):
            raise HydraError(f'Network directory {destination} does not exist')

        # Stop and remove service before removing network directory
        try:
            self.app.client.uninstall_systemd(name)
        except HydraError as exc:
            self.app.log.warning(exc)
            self.app.log.info(
                f'Service not installed.  Attempting to stop executable manually.')
            self.app.client.find_and_kill_executable(destination)

        # Remove network directory
        rmtree(destination)
        self.app.log.info(f'Removed network directory {destination}')

        # Remove .hydra_network if this is the default network
        default_network_file = self.app.utils.path('.hydra_network')

        if os.path.exists(default_network_file):
            remove_default_network = False

            with open(default_network_file, 'r') as network_file:
                default_network = network_file.readline().strip()
                if default_network == name:
                    remove_default_network = True

            if remove_default_network:
                os.remove(default_network_file)
                self.app.log.info(f'Removed default network setting')

        self.app.log.info(f'Successfully left network {name}')

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to stop',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
        ]
    )
    def stop_service(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['sudo', 'systemctl', 'stop', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'kill', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to start',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
        ]
    )
    def start_service(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['sudo', 'systemctl', 'start', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to restart',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
        ]
    )
    def restart_service(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['sudo', 'systemctl', 'stop', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'kill', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'start', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to get logs for',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
        ]
    )
    def logs(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['journalctl', '-u', name]
        os.execvp('journalctl', command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to get logs for',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-N', '--number'],
                {
                    'help': 'number of lines to get',
                    'action': 'store',
                    'dest': 'number'
                }
            ),
        ]
    )
    def tail_logs(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['journalctl', '-u', name, '--no-pager',
                   '-n', self.app.pargs.number or '100']
        os.execvp('journalctl', command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to get logs for',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-N', '--number'],
                {
                    'help': 'number of lines to get',
                    'action': 'store',
                    'dest': 'number'
                }
            ),
        ]
    )
    def follow_logs(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        command = ['journalctl', '-u', name, '-n',
                   self.app.pargs.number or '100', '-f']
        os.execvp('journalctl', command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to update',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (['node_name'],
                {
                        'help': 'the new name of the node',
                        'action': 'store'
                }
            ),
            (['description'],
                {
                        'help': 'the new description of the node',
                        'action': 'store'
                }
            ),
            (['website'],
                {
                        'help': 'the new website of the node',
                        'action': 'store'
                }
            )
        ]
    )
    def set_info(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)

        destination = self.app.utils.path(name)

        os.chdir(destination)

        self.app.log.info('Updating config.toml')
        with open('chaindata/config/config.toml', 'r') as config_toml:
            config = toml.load(config_toml, OrderedDict)

        config['moniker'] = self.app.pargs.node_name
        self.app.log.info(f'Editing config.toml: moniker = {config["moniker"]}')

        with open('chaindata/config/config.toml', 'w+') as config_toml:
            config_toml.write(toml.dumps(config))


        command = ['./shipchain', 'call', '-k', 'node_priv.key', 'update_candidate_info',
                    self.app.pargs.node_name, self.app.pargs.description, self.app.pargs.website]
        
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'stop', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'kill', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

        command = ['sudo', 'systemctl', 'start', name]
        self.app.log.info(' '.join(command))
        self.app.utils.binary_exec(*command)

    @ex(
        arguments=[
            (
                ['-n', '--name'],
                {
                    'help': 'name of network to get status for',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-H', '--host'],
                {
                    'help': 'host of network to get status',
                    'action': 'store',
                    'dest': 'host'
                }
            ),
            (
                ['-p', '--rpc-port'],
                {
                    'help': 'RPC port of network to get status',
                    'action': 'store',
                    'dest': 'rpc_port'
                }
            ),
            (
                ['-b', '--blocks'],
                {
                    'help': 'Number of prior blocks to scan for blocks validated by this node',
                    'action': 'store',
                    'dest': 'blocks',
                    'default': '250'
                }
            ),
        ]
    )
    def status(self):
        host = self.app.utils.env_or_arg(
            'host', 'HYDRA_NETWORK_HOST', or_path='.hydra_network_host') or 'localhost'
        port = self.app.utils.env_or_arg(
            'rpc_port', 'HYDRA_NETWORK_RPC_PORT', or_path='.hydra_network_rpc_port') or '46657'
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        size = int(self.app.pargs.blocks)

        # If user has signed a block in the past 100 blocks, it is a validator
        def get(stub):
            return json.loads(requests.get(f'http://{host}:{port}'+stub).content)

        status = get('/status')['result']
        net_info = get('/net_info')['result']

        latest_block = int(status['sync_info']['latest_block_height'])

        voted = 0
        for i in range(latest_block - size, latest_block):
            precommits = [
                    precommit for precommit in
                    get(f'/commit?height={i}')['result']['signed_header']['commit']['precommits']
                    if precommit and precommit['validator_address'] == status['validator_info']['address']
            ]
            if precommits:
                voted += 1

        outputs = OrderedDict()
        outputs['node_name'] = status['node_info']['moniker']

        outputs['node_block_height'] = status['sync_info']['latest_block_height']
        outputs['node_block_time'] = status['sync_info']['latest_block_time']

        if status['sync_info']['catching_up']:
            outputs['is_caught_up'] = False

            height_response = requests.post(f'https://{name}.network.shipchain.io:46658/query', json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getblockheight",
                "params": []
            })
            if height_response.status_code == 200:
                outputs['latest_block_height'] = height_response.json()['result']
                outputs['blocks_remaining'] = (int(height_response.json()['result']) -
                                               int(status['sync_info']['latest_block_height']))
        else:
            outputs['is_caught_up'] = True

        outputs['peer_count'] = net_info['n_peers']
        if int(outputs['peer_count']):
            outputs['peer_names'] = ', '.join([f"{peer['node_info']['moniker']}" for peer in net_info['peers']])
            
        if voted:
            # Yer a validator, 'arry!
            outputs['is_a_validator'] = True
            outputs['block_votes'] = voted
            outputs['block_sample'] = size
            outputs['vote_percentage'] = round((voted / size) * 100, 2)
        else:
            outputs['is_a_validator'] = False

        self.app.smart_render(outputs, 'key-value-print.jinja2')

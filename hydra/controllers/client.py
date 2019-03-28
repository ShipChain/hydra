import json
import os
from shutil import rmtree

from cement import Controller, ex
import requests
import yaml

YAML_LOAD = yaml.full_load if hasattr(yaml, 'full_load') else yaml.load


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
            cfg = YAML_LOAD(config_file)

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
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)

        if self.app.pargs.default:
            with open(self.app.utils.path('.hydra_network'), 'w+') as network_file:
                network_file.write(name)

        if os.path.exists(destination):
            if not self.app.pargs.destroy:
                self.app.log.error(f'Node directory exists, use -D to delete: {destination}')
                return
            rmtree(destination)

        if not self.app.pargs.version:
            url = f'{self.app.config["hydra"]["channel_url"]}/networks/{name}.json'
            try:
                remote_config = json.loads(requests.get(url).content)
                version = remote_config['binary_version']
            except json.JSONDecodeError:
                self.app.log.warning(f'Error getting network version details from {url}, using "latest"')
                version = "latest"
        else:
            version = self.app.pargs.version

        self.app.client.bootstrap(
            destination, version=version, destroy=self.app.pargs.destroy)

        if self.app.pargs.do_configure:
            self.app.client.configure(name, destination, version=self.app.pargs.version or 'latest')

        if self.app.pargs.install:
            self.app.client.install_systemd(name, destination, user=self.app.utils.binary_exec('whoami').stdout.strip())

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

        ]
    )
    def configure(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)

        if not os.path.exists(destination):
            self.app.log.error(f'Directory doesnt exist: {destination}')

        self.app.client.configure(name, destination, version=self.app.pargs.version or 'latest')

        if self.app.pargs.install:
            self.app.client.install_systemd(name, destination, user=self.app.utils.binary_exec('whoami').stdout.strip())

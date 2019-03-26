import json
import os
from shutil import rmtree

from cement import Controller, ex


class Client(Controller):
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
            return self.app.log.error('You must specify a --url')
        from yaml import load, dump, FullLoader

        with open(self.app.config_file, 'r+') as fh:
            cfg = load(fh, Loader=FullLoader)

        cfg['hydra']['channel_url'] = self.app.pargs.url

        open(self.app.config_file, 'w+').write(dump(cfg, indent=4))

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
        ]
    )
    def join_network(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network', required=True)
        destination = self.app.pargs.destination or self.app.utils.path(name)
        if (self.app.pargs.default):
            with open(self.app.utils.path('.hydra_network'), 'w+') as fh:
                fh.write(name)
        if os.path.exists(destination):
            if not self.app.pargs.destroy:
                self.app.log.error(
                    'Node directory exists, use -D to delete: %s' % destination)
                return
            rmtree(destination)

        if not self.app.pargs.version:
            url = '%s/networks/%s.json' % (
                self.app.config['hydra']['channel_url'], name)
            try:
                remote_config = json.loads(requests.get(url).content)
                version = remote_config['binary_version']
            except:
                remote_config = None
                self.app.log.warning(
                    'Error getting network version details from %s, using "latest"' % url)
                version = "latest"

        self.app.client.bootstrap(
            destination, version=version, destroy=self.app.pargs.destroy)

        if self.app.pargs.install:
            self.app.client.install_systemd(name, destination,
                                            user=self.app.utils.binary_exec('whoami').stdout.strip())

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
            self.app.log.error('Directory doesnt exist: %s' % destination)

        self.app.client.configure(
            name, destination, version=self.app.pargs.version or 'latest')

        if self.app.pargs.install:
            self.app.client.install_systemd(name, destination,
                                            user=self.app.utils.binary_exec('whoami').stdout.strip())

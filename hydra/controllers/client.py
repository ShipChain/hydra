from cement import Controller, ex
from datetime import datetime
from shutil import copy, rmtree
import os, json, stat

class Client(Controller):
    class Meta:
        label = 'client'
        stacked_on = 'base'
        stacked_type = 'nested'
        # text displayed at the top of --help output
        description = 'Client tools for connecting to remote networks'

    def client_exec(self, *args):
        os.chdir(self.app.client.path())
        self.app.log.debug('Running: ./shipchain '+' '.join(args))
        return self.app.client.exec(*args)

    @ex()
    def update(self):
        self.app.client.pip_update_hydra()

    @ex(
        arguments=[
            (
                ['-D', '--destroy'],
                {
                    'help': 'destroy existing directory',
                    'action': 'store_true',
                    'dest': 'destroy'
                }
            ),
        ]
    )
    def bootstrap(self):
        destination = self.app.utils.workdir('shipchain-network')
        bootstrap(self.app, destination, destroy=self.app.pargs.destroy)


def bootstrap(app, destination, version=None, destroy=False):
    if os.path.exists(destination):
        if not destroy:
            app.log.error('Node directory exists, use -D to delete: %s'%destination)
            return
        rmtree(destination)

    os.makedirs(destination)
    
    os.chdir(destination)

    app.utils.download_release_file('./shipchain', 'shipchain')

    os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)
        
    got_version = app.utils.binary_exec('./shipchain', 'version').stderr.strip()
    app.log.info('Copied ShipChain binary version %s'%got_version)

    app.log.info('Initializing Loom...')

    app.utils.binary_exec(['./shipchain', 'init'])

    validator = json.load(open(destination + '/chaindata/config/priv_validator.json'))

    app.log.info('Your validator address is:')
    app.log.info(validator['address'])
    app.log.info('Your validator public key is:')
    app.log.info(validator['pub_key']['value'])

    app.log.info('Writing hydra metadata...')
    metadata = {
        'bootstrapped': datetime.utcnow().strftime('%c'),
        'address': validator['address'],
        'pubkey': validator['pub_key']['value'],
        'shipchain_version': version,
        'by': 'hydra-bootstrap-devel'
    }
    json.dump(metadata, open(destination + '/.bootstrap.json', 'w+'), indent=2)


    app.log.info('Done!')

        

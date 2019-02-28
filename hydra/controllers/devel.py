from cement import Controller, ex
from datetime import datetime
from shutil import copy, rmtree
import os, json, stat

class Devel(Controller):
    class Meta:
        label = 'devel'
        stacked_on = 'base'
        stacked_type = 'nested'
        # text displayed at the top of --help output
        description = 'Development tools for launching local networks'

    def devel_exec(self, *args):
        os.chdir(self.app.devel.path())
        self.app.log.debug('Running: ./shipchain '+' '.join(args))
        return self.app.devel.exec(*args)

    @ex(
        help='Bootstrap a local ShipChain node for development',
        arguments= [
            (
                ['-D', '--destroy'],
                {
                    'help': 'destroy existing directory',
                    'action': 'store_true',
                    'dest': 'destroy'
                }
            ),
            (
                ['-S', '--start'],
                {
                    'help': 'start the loom node and replace the current process',
                    'action': 'store_true',
                    'dest': 'start'
                }
            )
        ]
    )
    def bootstrap(self):
        dev = self.app.devel.path()
        if os.path.exists(dev):
            if not self.app.pargs.destroy:
                self.app.log.error('Devel node directory exists, use -D to delete: %s'%dev)
                return
            rmtree(dev)

        os.makedirs(dev)
        
        os.chdir(self.app.devel.path())

        self.app.utils.download_release_file('./shipchain', 'shipchain')

        os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)
        
        version = self.devel_exec('version').stderr.strip()
        self.app.log.info('Copied ShipChain binary version %s'%version)

        self.app.log.info('Initializing Loom...')

        self.devel_exec('init')

        validator = json.load(open(self.app.devel.path('chaindata/config/priv_validator.json')))

        self.app.log.info('Your validator address is:')
        self.app.log.info(validator['address'])
        self.app.log.info('Your validator public key is:')
        self.app.log.info(validator['pub_key']['value'])

        self.app.log.info('Writing hydra metadata...')
        metadata = {
            'bootstrapped': datetime.utcnow().strftime('%c'),
            'address': validator['address'],
            'pubkey': validator['pub_key']['value'],
            'shipchain_version': version,
            'by': 'hydra-bootstrap-devel'
        }
        json.dump(metadata, open(self.app.devel.path('.bootstrap.json'), 'w+'), indent=2)


        self.app.log.info('Done!')

        if self.app.pargs.start:
            self.app.log.info('Starting blockchain service...')
            # Execvp will replace this process with the sidechain
            os.chdir(dev)
            os.execvp('./shipchain', ['./shipchain', 'run'])



        

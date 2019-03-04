import json
import os
import stat
import subprocess
import time
from datetime import datetime
from shutil import rmtree

import boto3
import requests
from colored import attr, fg
from pyfiglet import Figlet
from hydra.core.version import get_version

from . import HydraHelper

class ClientHelper(HydraHelper):
    def pip_update_hydra(self):
        pip = self.config.get('client', 'pip_install') % self.config['hydra']
        self.app.log.info('Updating pip from remote %s'%pip)
        # Execvp will replace this process with the sidechain
        os.execvp('pip3', ['pip3', 'install', pip])
    
    def bootstrap(self, destination, version=None, destroy=False):
        if os.path.exists(destination):
            if not destroy:
                self.app.log.error('Node directory exists, use -D to delete: %s'%destination)
                return
            rmtree(destination)

        os.makedirs(destination)
        
        os.chdir(destination)

        self.app.utils.download_release_file('./shipchain', 'shipchain')

        os.chmod('./shipchain', os.stat('./shipchain').st_mode | stat.S_IEXEC)
            
        got_version = self.app.utils.binary_exec('./shipchain', 'version').stderr.strip()
        self.app.log.debug('Copied ShipChain binary version %s'%got_version)

        self.app.log.info('Initializing Loom...')

        self.app.utils.binary_exec('./shipchain', 'init')
        node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()


        time.sleep(1) # Gotta wait a second because the priv_validator doesn't always show up

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
            'by': 'hydra-bootstrap-%s'%get_version()
        }
        json.dump(metadata, open('.bootstrap.json', 'w+'), indent=2)

        self.app.log.info('Bootstrapped!')

    def configure(self, name, destination, version=None):

        if not os.path.exists(destination):
            return self.app.log.error('Configuring client at destination does not exist: %s'%destination)

        url = '%s/networks/%s.json'%(self.app.config['hydra']['channel_url'], name)
        try:
            remote_config = json.loads(requests.get(url).content)
        except Exception as e:
            self.app.log.warning('Error getting network details from %s: %s'%(url, e))
            return

        os.chdir(destination)

        cd_genesis = json.load(open('chaindata/config/genesis.json'))
        cd_genesis['genesis_time'] = "1970-01-01T00:00:00Z"
        cd_genesis['validators'] = [
            {"name": "",
            "power": '10',
            "pub_key": {
                "type": "tendermint/PubKeyEd25519",
                "value": validator['pubkey']
            }}
            for ip, validator in remote_config['node_data'].items()
        ]
        json.dump(cd_genesis, open('chaindata/config/genesis.json', 'w+'), indent=4)
        
        genesis = json.load(open('genesis.json'))
        for i, contract in enumerate(genesis['contracts']):
            if contract['name'] == 'dpos':
                genesis['contracts'][i]['init']['params']['witnessCount'] = '51'
                genesis['contracts'][i]['init']['validators'] = [
                    {'pubKey': validator['pubkey'], 'power': '10'}
                    for ip, validator in remote_config['node_data'].items()
                ]
        json.dump(genesis, open('genesis.json', 'w+'), indent=4)
        node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()

        open('start_blockchain.sh', 'w+').write("""#!/bin/bash\n./shipchain run --persistent-peers %s"""
            %
                ','.join(
                    [
                        'tcp://%s@%s:46656'%(validator['nodekey'], ip)
                        for ip, validator in remote_config['node_data'].items()
                        if(validator['nodekey']) != node_key
            ]))


        self.app.log.info('Configured!')

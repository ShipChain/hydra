import json
import os
import stat
import subprocess
import time
import sys
from datetime import datetime
from shutil import rmtree
from collections import OrderedDict

import boto3
import requests
import toml

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

    def install_systemd(self, name, destination, user='ubuntu'):
        import toml
        systemd = OrderedDict([
            ('Unit', OrderedDict([
                ('Description', '%s Loom Node' % name),
                ('After', 'network.target'),
            ])),
            ('Service', OrderedDict([
                ('Type', 'simple'),
                ('User', user),
                ('WorkingDirectory', destination),
                ('ExecStart', '%s/start_blockchain.sh' % destination),
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
        local_fn = '%s.service' % name
        self.app.log.info('Writing to %s' % (local_fn))

        with open(local_fn, 'w+') as fh:
            # SystemD is just terse TOML - CHANGE MY MIND
            fh.write(toml.dumps(systemd).replace('"', '').replace(' = ', '='))
        
        fn = '/etc/systemd/system/%s.service' % name
        self.app.log.info('Installing %s as %s' % (fn, user))
        self.app.utils.binary_exec('sudo', 'cp', local_fn, fn)
        self.app.utils.binary_exec('sudo', 'chown', 'root:root', fn)
        self.app.utils.binary_exec('sudo', 'systemctl', 'daemon-reload')
        self.app.utils.binary_exec('sudo', 'systemctl', 'start', '%s.service'%name)
    
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

    def configure(self, name, destination, version=None, peers=None,
                    pex=True, address_book_strict=False, private_peers=False):

        if not os.path.exists(destination):
            return self.app.log.error('Configuring client at destination does not exist: %s'%destination)

        if not peers:
        # Get the published peering data
            url = '%s/networks/%s/hydra.json'%(self.app.config['hydra']['channel_url'], name)
            try:
                remote_config = json.loads(requests.get(url).content)
            except Exception as e:
                self.app.log.warning('Error getting network details from %s: %s'%(url, e))
                return
            peers = [(ip, validator['pubkey'], validator['nodekey'])
                    for ip, validator in remote_config['node_data'].items()]

        os.chdir(destination)

        # CHAINDATA/CONFIG/GENESIS.json

        url = '%s/networks/%s/chaindata/config/genesis.json'%(self.app.config['hydra']['channel_url'], name)
        try:
            cd_genesis = json.loads(requests.get(url).content)
        except Exception as e:
            self.app.log.warning('Error getting network details from %s: %s'%(url, e))
            return

        json.dump(cd_genesis, open('chaindata/config/genesis.json', 'w+'), indent=4)

        # GENESIS.json

        url = '%s/networks/%s/genesis.json'%(self.app.config['hydra']['channel_url'], name)
        try:
            genesis = json.loads(requests.get(url).content)
        except Exception as e:
            self.app.log.warning('Error getting network details from %s: %s'%(url, e))
            return

        json.dump(genesis, open('genesis.json', 'w+'), indent=4)

        this_node_key = self.app.utils.binary_exec('./shipchain', 'nodekey').stdout.strip()

        # CONFIG.TOML
        with open('chaindata/config/config.toml', 'r') as fh:
            config = toml.load(fh, OrderedDict)
        self.app.log.info('Editing config.toml: p2p.pex = %s' % pex)
        config['p2p']['pex'] = pex

        self.app.log.info('Editing config.toml: p2p.address_book_strict = %s' % address_book_strict)
        config['p2p']['address_book_strict'] = address_book_strict

        private_peers = private_peers and ','.join(
            [nodekey for ip, pub, nodekey in peers]) or ''
        self.app.log.info('Editing config.toml: p2p.private_peer_ids = %s' % private_peers)
        config['p2p']['private_peer_ids'] = private_peers

        proxy_app = 'tcp://0.0.0.0:46658'
        self.app.log.info('Editing config.toml: proxy_app = %s' % proxy_app)
        config['proxy_app'] = proxy_app

        rpc_laddr = 'tcp://0.0.0.0:46657'
        self.app.log.info('Editing config.toml: rpc.laddr = %s' % rpc_laddr)
        config['rpc']['laddr'] = rpc_laddr
        
        p2p_laddr = 'tcp://0.0.0.0:46656'
        self.app.log.info('Editing config.toml: p2p.laddr = %s' % p2p_laddr)
        config['p2p']['laddr'] = p2p_laddr

        with open('chaindata/config/config.toml', 'w+') as fh:
            fh.write(toml.dumps(config))
        
        # START_BLOCKCHAIN.sh
        open('start_blockchain.sh', 'w+').write(
            "#!/bin/bash\n./shipchain run --persistent-peers %s\n"
            %
                ','.join(
                    [
                        'tcp://%s@%s:46656'%(nodekey, ip)
                        for ip, pubkey, nodekey in peers
                        if nodekey != this_node_key
            ]))

        os.chmod('./start_blockchain.sh', os.stat('./start_blockchain.sh').st_mode | stat.S_IEXEC)

        self.app.log.info('Configured!')

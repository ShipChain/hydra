from cement import Controller, ex, shell
from datetime import datetime
from shutil import copy, rmtree
from troposphere import Ref, Template, ec2, Parameter, Output, GetAtt
from troposphere.ec2 import NetworkInterfaceProperty
import os
import json
import uuid
import time
import paramiko
import io
import glob

NAME_ARG = (['--name'], {'help': 'the name of the network to run on',
                         'action': 'store', 'dest': 'name'})


class Network(Controller):
    class Meta:
        label = 'network'
        stacked_on = 'base'
        stacked_type = 'nested'

        # text displayed at the top of --help output
        description = 'Troposphere remote launch tools'

    @ex(
        help='Provision a new ShipChain network',
        arguments=[
            NAME_ARG,
            (
                ['-s', '--size'],
                {
                    'help': 'the number of new nodes to launch',
                    'action': 'store',
                    'dest': 'size'
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
        ]
    )
    def provision(self):
        size = int(self.app.pargs.size or 1)
        name = self.app.pargs.name or '%s-network-%s' % (
            self.app.project, str(uuid.uuid4())[:6])

        if not 'aws_ec2_key_name' in self.app.config['provision']:
            self.app.log.error(
                'You need to set provision.aws_ec2_key_name in the config')
            return

        self.app.log.info('Starting new network: %s' % name)
        template = Template()
        sg, subnet, vpc = self.app.networks.sg_subnet_vpc(template)

        for i in range(size):
            self.app.networks.add_instance(name, template, i, sg, subnet)

        tpl = template.to_json()

        cf = self.app.networks.get_boto().resource('cloudformation')
        stack = cf.create_stack(
            StackName=name,
            TemplateBody=tpl
        )

        self.app.log.info('Waiting for cloudformation: %s' % name)

        while True:
            stack.reload()
            print('Status: ', stack.stack_status)
            REGISTRY = {
                'bootstrapped': datetime.utcnow().strftime('%c'),
                'size': size,
                'status': stack.stack_status
            }
            self.app.networks.register(name, REGISTRY)

            if stack.stack_status.startswith('ROLLBACK_'):
                p = shell.Prompt('Error deploying cloudformation, what do you want to do?',
                                 options=['Delete It', 'Leave It'], numbered=True
                                 )
                if p.prompt() == 'Delete It':
                    stack.delete()
                return
            if stack.stack_status == 'CREATE_COMPLETE':
                if(self.app.pargs.default):
                    with open(self.app.utils.path('.hydra_network'), 'w+') as fh:
                        fh.write(name)

                outputs = {o['OutputKey']: o['OutputValue']
                           for o in stack.outputs}
                ips = [outputs['IP%s' % i] for i in range(size)]

                REGISTRY['outputs'], REGISTRY['ips'] = outputs, ips
                self.app.networks.register(name, REGISTRY)

                for ip in ips:
                    self.app.log.info('Node IP: %s' % ip)

                self.app.log.info(
                    'Creation complete, pausing for a minute while the software installs...')
                for i in range(10):
                    time.sleep(30)
                    try:
                        REGISTRY['node_data'] = {
                            ip: self.get_bootstrap_data(ip, name) for ip in ips
                        }
                        self.app.networks.register(name, REGISTRY)
                        break
                    except:
                        pass

                self.app.log.info('Stack launch success!')

                return True

            time.sleep(10)

    @ex(
        help='SSH into the first available node',
        arguments=[NAME_ARG]
    )
    def ssh_first_node(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()
        ip = networks[name or list(networks.keys())[0]]['ips'][0]
        os.execvp('ssh', ['ssh', 'ubuntu@%s' % ip])

    @ex(
        help='Run on all nodes',
        arguments=[
            NAME_ARG,
            (
                ['-c', '--cmd'],
                {
                    'help': 'command to run',
                    'action': 'store',
                    'dest': 'cmd'
                }
            ),
        ]
    )
    def run_on_all_nodes(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()

        for ip in networks[name]['ips']:
            self.app.networks.run_command(ip, self.app.pargs.cmd)

    def get_bootstrap_data(self, ip, network_name):
        return json.loads(self.app.networks.run_command(ip, 'cat %s/.bootstrap.json' % network_name))

    def _deprovision(self, network_name):
        self.app.log.info('Deleting network: %s' % network_name)

        self.app.networks.deregister(network_name)

        cf = self.app.networks.get_boto().resource('cloudformation')
        try:
            cf.Stack(network_name).delete()
        except Exception as e:
            self.app.log.warning('Error deleting stack: %s' % e)

    @ex(help="destroy all registered cloudformation stacks")
    def deprovision_all(self):
        for k, options in self.app.networks.read_networks_file().items():
            if options.get('bootstrapped', ''):
                self._deprovision(k)

    @ex(help="destroy a registered cloudformation stack",
        arguments=[NAME_ARG,]
    )
    def deprovision(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()
        if not name in networks:
            return self.app.log.error('You must choose a valid network name: %s' % networks.keys())
        self._deprovision(name)

    @ex(help='Publish the network details to S3',
        arguments=[
             NAME_ARG,
            (
                ['-v', '--version'],
                {
                    'help': 'version to publish',
                    'action': 'store',
                    'dest': 'version'
                }
            ),
        ])
    def publish(self):
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()
        if not name in networks:
            return self.app.log.error('You must choose a valid network name: %s' % networks.keys())
        network = networks[name]
        network['version'] = self.app.pargs.version or 'latest'
        os.chdir(self.app.utils.path())
        os.makedirs('./networks/%s' % name, exist_ok=True)
        self.app.networks.bootstrap_config(name)

        local_fn = 'networks/%s/hydra.json' % name
        open(local_fn, 'w+').write(json.dumps(network))
        s3 = self.app.release.get_boto().resource('s3')
        self.app.log.info('Publishing network %s' % name)
        for fn in ['chaindata/config/genesis.json', 'hydra.json', 'genesis.json']:
            local_fn = 'networks/%s/%s' % (name, fn)
            self.app.log.debug('Uploading: %s to S3' % (local_fn))
            s3.Bucket(self.app.release.dist_bucket).upload_file(
                Filename=local_fn, Key=local_fn, ExtraArgs={'ACL': 'public-read'})

    @ex(help='configure',
        arguments=[
             (
                 ['--name'],
                 {
                     'help': 'the name of the network to run on',
                     'action': 'store',
                     'dest': 'name'
                 }
             ),
        ])
    def configure(self):
        networks = self.app.networks.read_networks_file()
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')
        if not name in networks:
            return self.app.log.error('You must choose a valid network name: %s' % networks.keys())

        for ip in networks[name]['ips']:
            self.app.networks.run_command(
                ip, "hydra client configure --name=%s" % name)

import json
import os
import time
import uuid
from datetime import datetime

from cement import Controller, ex, shell
from troposphere import Template

NAME_ARG = (
    ['--name'],
    {
        'help': 'the name of the network to run on',
        'action': 'store',
        'dest': 'name'
    }
)


class Network(Controller):  # pylint: disable=too-many-ancestors
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
            (
                    ['-v', '--version'],
                    {
                        'help': 'version of network software to run',
                        'action': 'store',
                        'dest': 'version'
                    }
            ),
        ]
    )
    def provision(self):
        node_count = int(self.app.pargs.size or 1)
        version = self.app.pargs.version or None
        name = self.app.pargs.name or f'{self.app.project}-network-{str(uuid.uuid4())[:6]}'

        if 'aws_ec2_key_name' not in self.app.config['provision']:
            self.app.log.error(
                'You need to set provision.aws_ec2_key_name in the config')
            return

        self.app.log.info(f'Starting new network: {name}')
        template = Template()
        security_group, subnet = self.app.networks.sg_subnet_vpc(template)

        for instance_num in range(node_count):
            self.app.networks.add_instance(name, template, instance_num, security_group, subnet, version)

        template_json = template.to_json()

        cloud_formation = self.app.networks.get_boto().resource('cloudformation')
        stack = cloud_formation.create_stack(
            StackName=name,
            TemplateBody=template_json
        )

        self.app.log.info(f'Waiting for cloudformation: {name}')

        self.monitor_cloud_formation_stack(stack, node_count, name)

    def monitor_cloud_formation_stack(self, stack, node_count, name):
        while True:
            stack.reload()
            print('Status: ', stack.stack_status)
            registry = {
                'bootstrapped': datetime.utcnow().strftime('%c'),
                'size': node_count,
                'status': stack.stack_status
            }
            self.app.networks.register(name, registry)

            if stack.stack_status.startswith('ROLLBACK_'):
                user_response = shell.Prompt('Error deploying cloudformation, what do you want to do?',
                                             options=['Delete It', 'Leave It'],
                                             numbered=True)
                if user_response.prompt() == 'Delete It':
                    stack.delete()
                return

            if stack.stack_status == 'CREATE_COMPLETE':
                if self.app.pargs.default:
                    with open(self.app.utils.path('.hydra_network'), 'w+') as network_file:
                        network_file.write(name)

                outputs = {o['OutputKey']: o['OutputValue'] for o in stack.outputs}
                ips = [outputs[f'IP{node}'] for node in range(node_count)]

                registry['outputs'], registry['ips'] = outputs, ips
                self.app.networks.register(name, registry)

                for ip in ips:
                    self.app.log.info(f'Node IP: {ip}')

                self.app.log.info('Creation complete, pausing for a minute while the software installs...')

                for attempt in range(10):
                    time.sleep(30)
                    self.app.log.info(f'Bootstrapping node: {attempt}')
                    try:
                        registry['node_data'] = {ip: self.get_bootstrap_data(ip, name) for ip in ips}
                        self.app.networks.register(name, registry)
                        break
                    except:  # pylint: disable=bare-except
                        pass

                if attempt >= 10:
                    self.app.log.error('Timed out waiting for nodes to bootstrap.')
                else:
                    self.app.log.info('Stack launch success!')
                return

            time.sleep(10)

    @ex(
        help='SSH into the first available node',
        arguments=[NAME_ARG]
    )
    def ssh_first_node(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()
        ip = networks[name or list(networks.keys())[0]]['ips'][0]
        os.execvp('ssh', ['ssh', f'ubuntu@{ip}'])

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
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()

        for ip in networks[name]['ips']:
            self.app.networks.run_command(ip, self.app.pargs.cmd)

    def get_bootstrap_data(self, ip, network_name):
        return json.loads(self.app.networks.run_command(ip, f'cat {network_name}/.bootstrap.json'))

    def _deprovision(self, network_name):
        self.app.log.info(f'Deleting network: {network_name}')

        self.app.networks.deregister(network_name)

        cloud_formation = self.app.networks.get_boto().resource('cloudformation')
        try:
            cloud_formation.Stack(network_name).delete()
        except Exception as exc:  # pylint: disable=broad-except
            self.app.log.warning(f'Error deleting stack: {exc}')

    @ex(help="destroy all registered cloudformation stacks")
    def deprovision_all(self):
        for network_name, options in self.app.networks.read_networks_file().items():
            if options.get('bootstrapped', ''):
                self._deprovision(network_name)

    @ex(
        help="destroy a registered cloudformation stack",
        arguments=[NAME_ARG, ]
    )
    def deprovision(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()
        if name not in networks:
            self.app.log.error(f'You must choose a valid network name: {networks.keys()}')
            return
        self._deprovision(name)

    @ex(
        help='Publish the network details to S3',
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
        ]
    )
    def publish(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.networks.read_networks_file()

        if name not in networks:
            self.app.log.error(f'You must choose a valid network name: {networks.keys()}')
            return

        network = networks[name]
        network['version'] = self.app.pargs.version or 'latest'

        os.chdir(self.app.utils.path())
        os.makedirs(f'./networks/{name}', exist_ok=True)
        self.app.networks.bootstrap_config(name)

        local_fn = f'networks/{name}/hydra.json'
        open(local_fn, 'w+').write(json.dumps(network))
        s3 = self.app.release.get_boto().resource('s3')

        self.app.log.info(f'Publishing network {name}')

        for file_name in ['chaindata/config/genesis.json', 'hydra.json', 'genesis.json']:
            local_fn = f'networks/{name}/{file_name}'
            self.app.log.debug(f'Uploading: {local_fn} to S3')
            s3.Bucket(self.app.release.dist_bucket).upload_file(Filename=local_fn,
                                                                Key=local_fn,
                                                                ExtraArgs={'ACL': 'public-read'})

    @ex(
        help='configure',
        arguments=[
            (
                    ['--name'],
                    {
                        'help': 'the name of the network to run on',
                        'action': 'store',
                        'dest': 'name'
                    }
            ),
        ]
    )
    def configure(self):
        networks = self.app.networks.read_networks_file()
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')

        if name not in networks:
            self.app.log.error(f'You must choose a valid network name: {networks.keys()}')
            return

        for ip in networks[name]['ips']:
            self.app.networks.run_command(ip, f'hydra client configure --name={name}')

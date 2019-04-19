import json
import os
import random
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
        cfn = self.app.network.sg_subnet_vpc(template)

        instances = []
        for instance_num in range(node_count):
            instances.append(self.app.network.add_instance(name, template, instance_num, cfn['ec2_sg'],
                                                           random.choice([cfn['subnet_az_1'], cfn['subnet_az_2']]),
                                                           version))

        alb = self.app.network.add_alb(template, cfn['vpc'], cfn['alb_sg'], cfn['subnet_az_1'], cfn['subnet_az_2'],
                                       instances)
        self.app.network.add_route53(name, template, alb)

        template_json = template.to_json()

        cloud_formation = self.app.network.get_boto().resource('cloudformation')
        stack = cloud_formation.create_stack(
            StackName=name,
            TemplateBody=template_json
        )

        self.app.log.info(f'Waiting for cloudformation: {name}')

        self.monitor_cloudformation_stack(stack, node_count, name)

    def monitor_cloudformation_stack(self, stack, node_count, name):
        stack.reload()
        registry = {
            'bootstrapped': datetime.utcnow().strftime('%c'),
            'size': node_count,
            'status': stack.stack_status,
            'outputs': {},
            'ips': [],
            'node_data': {}
        }
        self.app.network.register(name, registry)
        while stack.stack_status == 'CREATE_IN_PROGRESS':
            self.app.log.info(f'Status: {stack.stack_status}')
            time.sleep(10)
            stack.reload()
            registry['status'] = stack.stack_status
            self.app.network.register(name, registry)
        self.app.log.info(f'Status: {stack.stack_status}')

        if stack.stack_status != 'CREATE_COMPLETE':
            user_response = shell.Prompt('Error deploying cloudformation, what do you want to do?',
                                         options=['Delete It', 'Leave It'],
                                         numbered=True)
            if user_response.prompt() == 'Delete It':
                stack.delete()
            return

        if self.app.pargs.default:
            with open(self.app.utils.path('.hydra_network'), 'w+') as network_file:
                network_file.write(name)

        registry['outputs'] = {o['OutputKey']: o['OutputValue'] for o in stack.outputs}
        for node in range(node_count):
            ip = registry['outputs'][f'IP{node}']
            registry['ips'].append(ip)
            self.app.log.info(f'Node IP: {ip}')

        self.app.network.register(name, registry)

        self.app.log.info('Creation complete, pausing for a minute while the software installs...')

        for ip in registry['ips']:
            for attempt in range(1, 11):
                try:
                    self.app.log.info(f'Bootstrapping node {ip} attempt {attempt}...')
                    registry['node_data'][ip] = self.get_bootstrap_data(ip, name)
                    break
                except Exception:  # pylint: disable=broad-except
                    if attempt >= 10:
                        self.app.log.error(f'Timed out waiting for node {ip} to bootstrap.')
                        break
                    time.sleep(30)
                    continue

        registry['bootstrapped'] = datetime.utcnow().strftime('%c')
        self.app.network.register(name, registry)

        self.app.log.info('Stack launch success!')

    @ex(
        help='SSH into the first available node',
        arguments=[NAME_ARG]
    )
    def ssh_first_node(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.network.read_networks_file()
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
        networks = self.app.network.read_networks_file()

        for ip in networks[name]['ips']:
            self.app.network.run_command(ip, self.app.pargs.cmd)

    def get_bootstrap_data(self, ip, network_name):
        return json.loads(self.app.network.run_command(ip, f'cat {network_name}/.bootstrap.json'))

    def _deprovision(self, network_name):
        self.app.log.info(f'Deleting network: {network_name}')

        self.app.network.deregister(network_name)

        cloud_formation = self.app.network.get_boto().resource('cloudformation')
        try:
            cloud_formation.Stack(network_name).delete()
        except Exception as exc:  # pylint: disable=broad-except
            self.app.log.warning(f'Error deleting stack: {exc}')

    @ex(help="destroy all registered cloudformation stacks")
    def deprovision_all(self):
        for network_name, options in self.app.network.read_networks_file().items():
            if options.get('bootstrapped', ''):
                self._deprovision(network_name)

    @ex(
        help="destroy a registered cloudformation stack",
        arguments=[NAME_ARG, ]
    )
    def deprovision(self):
        name = self.app.utils.env_or_arg('name', 'HYDRA_NETWORK', or_path='.hydra_network')
        networks = self.app.network.read_networks_file()
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
        networks = self.app.network.read_networks_file()

        if name not in networks:
            self.app.log.error(f'You must choose a valid network name: {networks.keys()}')
            return

        network = networks[name]
        network['version'] = self.app.pargs.version or 'latest'

        os.chdir(self.app.utils.path())
        os.makedirs(f'./networks/{name}', exist_ok=True)
        self.app.network.bootstrap_config(name)

        local_fn = f'networks/{name}/hydra.json'
        open(local_fn, 'w+').write(json.dumps(network))
        s3 = self.app.release.get_boto().resource('s3')

        self.app.log.info(f'Publishing network {name}')

        for file_name in ['chaindata/config/genesis.json', 'hydra.json', 'genesis.json', 'loom.yaml']:
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
            (
                    ['--oracle-priv-key'],
                    {
                        'help': 'the path of eth private key of the oracle mainnet account',
                        'action': 'store',
                        'dest': 'oracle_eth_priv',
                        'required': True
                    }
            ),
        ]
    )
    def configure(self):
        networks = self.app.network.read_networks_file()
        name = self.app.utils.env_or_arg(
            'name', 'HYDRA_NETWORK', or_path='.hydra_network')

        if name not in networks:
            self.app.log.error(f'You must choose a valid network name: {networks.keys()}')
            return

        for index, ip in enumerate(networks[name]['ips']):
            if index == 0:
                self.app.network.scp(ip, self.app.pargs.oracle_eth_priv, f'{name}/oracle_eth_priv.key')
            self.app.network.run_command(ip, f'hydra client configure '
                                             f'--name={name}{" --as-oracle" if index == 0 else ""} 2>&1')

        # Wait for network to activate chainconfig
        time.sleep(10)

        registration_requirement = self.app.config['provision']['dpos']['registration_requirement']
        lock_time = self.app.config['provision']['dpos']['lock_time']
        fee = self.app.config['provision']['dpos']['fee']
        for index, ip in enumerate(networks[name]['ips']):
            if index == 0:
                self.app.network.run_command(ip, f"cd {name}; ./shipchain call set_registration_requirement "
                                             f"{registration_requirement} -k node_priv.key")

                for node in networks[name]['node_data']:
                    address = networks[name]['node_data'][node]['hex_address']
                    self.app.network.run_command(ip, f'cd {name}; ./shipchain call whitelist_candidate {address} '
                                                 f'{registration_requirement} 0 -k node_priv.key')
            pubkey = networks[name]['node_data'][ip]['pubkey']
            self.app.network.run_command(ip, f'cd {name}; ./shipchain call register_candidateV2 {pubkey} '
                                         f'{fee} {lock_time} --name shipchain-node-{index + 1} -k node_priv.key')

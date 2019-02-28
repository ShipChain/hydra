from cement import Controller, ex, shell
from datetime import datetime
from shutil import copy, rmtree
from troposphere import Ref, Template, ec2, Parameter, Output, GetAtt
from troposphere.ec2 import NetworkInterfaceProperty
import os, json, uuid, time
import paramiko
import io
class Network(Controller):
    class Meta:
        label = 'network'
        stacked_on = 'base'
        stacked_type = 'nested'

        # text displayed at the top of --help output
        description = 'Troposphere remote launch tools'

    @ex(
        help='Provision a new ShipChain network',
        arguments= [
            (
                ['--id'],
                {
                    'help': 'the new ID of the network',
                    'action': 'store',
                    'dest': 'id'
                }
            ),
            (
                ['-n', '--nodes'],
                {
                    'help': 'the number of new nodes to launch',
                    'action': 'store',
                    'dest': 'nodes'
                }
            ),
        ]
    )
    def provision(self):
        id = self.app.pargs.id or str(uuid.uuid4())[:6]
        nodes = int(self.app.pargs.nodes or 1)
        stack_name = '%s-network-%s'%(self.app.project, id)

        if not 'aws_ec2_key_name' in self.app.config['provision']:
            self.app.log.error('You need to set provision.aws_ec2_key_name in the config')
            return

        self.app.log.info('Starting new network: %s' % stack_name)
        template = Template()
        sg, subnet, vpc = self.app.networks.sg_subnet_vpc(template)

        for i in range(nodes):
            self.app.networks.add_instance(template, i, sg, subnet)        

        tpl = template.to_json()

        cf = self.app.networks.get_boto().resource('cloudformation')
        stack = cf.create_stack(
            StackName=stack_name,
            TemplateBody=tpl
        )

        self.app.log.info('Waiting for cloudformation: %s' % stack_name)

        while True:
            stack.reload()
            if stack.stack_status.startswith('ROLLBACK_'):
                p = shell.Prompt('Error deploying cloudformation, what do you want to do?',
                    options=['Delete It', 'Leave It'], numbered=True
                )
                if p.prompt() == 'Delete It':
                    stack.delete()
                return
            print(stack.stack_status)
            if stack.stack_status == 'CREATE_COMPLETE':
                outputs = {o['OutputKey']: o['OutputValue'] for o in stack.outputs}
                ips = [outputs['IP%s'%i] for i in range(nodes)]
                self.app.networks.register(stack_name, {
                    'bootstrapped': datetime.utcnow().strftime('%c'),
                    'size': nodes,
                    'outputs': outputs,
                    'ips': ips
                })
                self.app.log.info('Stack launch success!')
                for ip in ips:
                    self.app.log.debug('Node IP: %s'%ip)

                self.app.log.info('You can SSH into your head node with:')
                self.app.log.info('ssh ubuntu@%s'%ips[0])
                break
            time.sleep(10)

    @ex(
        help='SSH into the first available node',
        arguments= [
            (
                ['--id'],
                {
                    'help': 'the ID of the network to run on',
                    'action': 'store',
                    'dest': 'id'
                }
            )
        ]
    )
    def ssh_first_node(self):
        networks = self.app.networks.read_networks_file()
        ip = networks[self.app.pargs.id or list(networks.keys())[0]]['ips'][0]
        os.execvp('ssh', ['ssh', 'ubuntu@%s'%ip])
        
    @ex(
        help='Run on all nodes',
        arguments= [
            (
                ['--id'],
                {
                    'help': 'the ID of the network to run on',
                    'action': 'store',
                    'dest': 'id'
                }
            ),
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
        networks = self.app.networks.read_networks_file()

        for ip in networks[self.app.pargs.id]['ips']:
            self.run_command(ip, self.app.pargs.cmd)

    def run_command(self, ip, cmd):    
        DEFAULT_KEY = '~/.ssh/%(aws_ec2_key_name)s.pem'
        provision = self.app.config['provision']
        KEY = os.path.expanduser(
            'aws_ec2_key_path' in provision and
            provision['aws_ec2_key_path'] % provision or
            DEFAULT_KEY % provision
        )

        self.app.log.info('Running on %s: %s' % (ip, cmd))
        self.app.log.debug('Using keyfile: %s' % KEY)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)
        client.connect(ip, username='ubuntu', key_filename=KEY)
        stdin, stdout, stderr = client.exec_command(cmd or 'ls')
        for line in stderr:
            print('... ' + line.strip('\n'))
        for line in stdout:
            print('... ' + line.strip('\n'))
        client.close() 

    

    def deprovision(self, network_name):
        self.app.log.info('Deleting network: %s' % network_name)
        
        self.app.networks.deregister(network_name)

        cf = self.app.utils.get_boto().resource('cloudformation')
        try:
            cf.Stack(network_name).delete()
        except Exception as e:
            self.app.log.warning('Error deleting stack: %s'%e)

    @ex()
    def deprovision_all(self):
        for k, options in self.read_networks_file().items():
            if options.get('bootstrapped', ''):
                self.deprovision(k)
    
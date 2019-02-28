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
                ['--name'],
                {
                    'help': 'the new name of the network',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
            (
                ['-s', '--size'],
                {
                    'help': 'the number of new nodes to launch',
                    'action': 'store',
                    'dest': 'size'
                }
            ),
        ]
    )
    def provision(self):
        size = int(self.app.pargs.size or 1)
        name = self.app.pargs.name or '%s-network-%s'%(self.app.project, str(uuid.uuid4())[:6])

        if not 'aws_ec2_key_name' in self.app.config['provision']:
            self.app.log.error('You need to set provision.aws_ec2_key_name in the config')
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
                
                outputs = {o['OutputKey']: o['OutputValue'] for o in stack.outputs}
                ips = [outputs['IP%s'%i] for i in range(size)]

                REGISTRY['outputs'], REGISTRY['ips'] = outputs, ips
                self.app.networks.register(name, REGISTRY)

                for ip in ips:
                    self.app.log.info('Node IP: %s'%ip)

                self.app.log.info('Creation complete, pausing for a minute while the software installs...')
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

                break
            time.sleep(10)

    @ex(
        help='SSH into the first available node',
        arguments= [
            (
                ['--name'],
                {
                    'help': 'the name of the network to run on',
                    'action': 'store',
                    'dest': 'name'
                }
            )
        ]
    )
    def ssh_first_node(self):
        networks = self.app.networks.read_networks_file()
        ip = networks[self.app.pargs.name or list(networks.keys())[0]]['ips'][0]
        os.execvp('ssh', ['ssh', 'ubuntu@%s'%ip])
        
    @ex(
        help='Run on all nodes',
        arguments= [
            (
                ['--name'],
                {
                    'help': 'the name of the network to run on',
                    'action': 'store',
                    'dest': 'name'
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

        for ip in networks[self.app.pargs.name]['ips']:
            self.run_command(ip, self.app.pargs.cmd)

    def run_command(self, ip, cmd):    
        import warnings
        DEFAULT_KEY = '~/.ssh/%(aws_ec2_key_name)s.pem'
        provision = self.app.config['provision']
        KEY = os.path.expanduser(
            'aws_ec2_key_path' in provision and
            provision['aws_ec2_key_path'] % provision or
            DEFAULT_KEY % provision
        )

        self.app.log.info('Running on %s: %s' % (ip, cmd))
        self.app.log.debug('Using keyfile: %s' % KEY)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)
            client.connect(ip, username='ubuntu', key_filename=KEY)
            stdin, stdout, stderr = client.exec_command(cmd or 'ls')
            output = ''.join(line for line in stdout)
            client.close()
            self.app.log.debug('Output: %s' % output)
            return output

    def get_bootstrap_data(self, ip, network_name):
        return json.loads(self.run_command(ip, 'cat %s/.bootstrap.json'%network_name))

    def deprovision(self, network_name):
        self.app.log.info('Deleting network: %s' % network_name)
        
        self.app.networks.deregister(network_name)

        cf = self.app.networks.get_boto().resource('cloudformation')
        try:
            cf.Stack(network_name).delete()
        except Exception as e:
            self.app.log.warning('Error deleting stack: %s'%e)

    @ex()
    def deprovision_all(self):
        for k, options in self.app.networks.read_networks_file().items():
            if options.get('bootstrapped', ''):
                self.deprovision(k)

    @ex( help='Publish the network details to S3',
        arguments= [
            (
                ['--name'],
                {
                    'help': 'the name of the network to run on',
                    'action': 'store',
                    'dest': 'name'
                }
            ),
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
        networks = self.app.networks.read_networks_file()
        if not self.app.pargs.name in networks:
            return self.app.log.error('You must choose a valid network name: %s'%networks.keys())
        
        network = networks[self.app.pargs.name]
        network['version'] = self.app.pargs.version or 'latest'
        os.chdir(self.app.utils.path())
        os.makedirs('./networks/', exist_ok=True)
        local_fn = 'networks/%s.json'%self.app.pargs.name
        open(local_fn, 'w+').write(json.dumps(network))
        s3 = self.app.release.get_boto().resource('s3')
        self.app.log.info('Publishing network %s' % self.app.pargs.name)
        self.app.log.debug('Uploading: networks/%s.json to S3' % (local_fn))
        s3.Bucket(self.app.release.dist_bucket).upload_file(
            Filename=local_fn, Key=local_fn, ExtraArgs={'ACL':'public-read'})

    @ex( help='configure',
        arguments= [
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
        name = self.app.pargs.name
        if not name in networks:
            return self.app.log.error('You must choose a valid network name: %s'%networks.keys())
        
        for ip in networks[name]['ips']:
            self.run_command(ip, "hydra client configure --name=%s"%name)
    
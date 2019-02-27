from cement import Controller, ex
from datetime import datetime
from shutil import copy, rmtree
from troposphere import Ref, Template, ec2, Parameter, Output, GetAtt
from troposphere.ec2 import NetworkInterfaceProperty
import os, json, uuid, time

class Network(Controller):
    class Meta:
        label = 'network'
        stacked_on = 'base'
        stacked_type = 'nested'

        # text displayed at the top of --help output
        description = 'Troposphere remote launch tools'

    @ex(
        help='Bootstrap a new ShipChain network',
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
    def bootstrap(self):
        id = self.app.pargs.id or str(uuid.uuid4())[:6]
        nodes = int(self.app.pargs.nodes or 1)

        if not 'aws_ec2_key_name' in self.app.config['hydra']:
            self.app.log.error('You need to set hydra.aws_ec2_key_name in the config')
            return

        self.app.log.info('Starting new network: %s' % id)
        template = Template()
        sg, subnet, vpc = self.app.tropo.sg_subnet_vpc(template)

        for i in range(nodes):
            self.app.tropo.add_instance(template, i, sg, subnet)


        

        tpl = template.to_json()

        cf = self.app.utils.get_boto().resource('cloudformation')
        stack_name = 'shipchain-network-%s'%id
        stack = cf.create_stack(
            StackName=stack_name,
            TemplateBody=tpl
        )

        self.app.log.info('Waiting for cloudformation: %s' % id)

        while True:
            stack.reload()
            if stack.stack_status.startswith('ROLLBACK_'):
                self.app.log.error('Error deploying cloudformation, do you want to delete the stack?')
                print(' [Y/n] ')
                if input().lower() in {'yes', 'y', 'ye', ''}:
                    self.app.log.error('Deleting your stack...')
                    stack.delete()
                return
            print(stack.stack_status)
            if stack.stack_status == 'CREATE_COMPLETE':
                break
            
            time.sleep(10)
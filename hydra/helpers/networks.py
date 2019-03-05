import json
import os
import paramiko
from . import HydraHelper
import boto3

class NetworksHelper(HydraHelper):
    def read_networks_file(self):
        try:
            return json.load(open(self.app.utils.path('networks.json'), 'r+'))
        except:
            return {}
        
    def register(self, network_name, options):
        networks = self.read_networks_file()

        networks[network_name] = options or {}

        json.dump(networks, open(self.app.utils.path('networks.json'), 'w+'))

    def deregister(self, network_name):
        networks = self.read_networks_file()

        if network_name in networks:
            networks.pop(network_name, '')
            self.app.log.info('Deregistering network: %s' % network_name)

        json.dump(networks, open(self.app.utils.path('networks.json'), 'w+'))
    
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
            stdin, stdout, stderr = client.exec_command(cmd)
            output = ''.join(line for line in stdout)
            client.close()
            self.app.log.debug('Output: %s' % output)
            return output

    def bootstrap_config(self, network_name):
        networks = self.read_networks_file()
        network = networks[network_name]
        folder = 'networks/%s'%network_name

        def open_first_file(fn):
            ip = network['ips'][0]
            output = self.run_command(ip, "cat %s/%s"%(network_name, fn))
            return output

        peers = [(ip, validator['pubkey'], validator['nodekey'])
                for ip, validator in network['node_data'].items()]

        os.makedirs('networks/%s/chaindata/config/'%network_name, exist_ok=True)
        cd_genesis = json.loads(open_first_file('chaindata/config/genesis.json'))
        cd_genesis['genesis_time'] = "1970-01-01T00:00:00Z"
        cd_genesis['validators'] = [
            {"name": "",
            "power": '1000',
            "pub_key": {
                "type": "tendermint/PubKeyEd25519",
                "value": pubkey
            }}
            for ip, pubkey, nodekey in peers
        ]
        json.dump(cd_genesis, open('%s/chaindata/config/genesis.json'%folder, 'w+'), indent=4)

        # GENESIS.json

        genesis = json.loads(open_first_file('genesis.json'))
        for i, contract in enumerate(genesis['contracts']):
            if contract['name'] == 'dpos':
                genesis['contracts'][i]['init']['params']['witnessCount'] = '51'
                genesis['contracts'][i]['init']['validators'] = [
                    {'pubKey': pubkey, 'power': '1000'}
                    for ip, pubkey, nodekey in peers
                ]
        json.dump(genesis, open('%s/genesis.json'%folder, 'w+'), indent=4)
        

    def get_boto(self):
        return boto3.Session(profile_name=self.config.get('provision', 'aws_profile'))

    def add_instance(self, stack_name, t, i, sg, subnet):
        from troposphere import Base64, FindInMap, GetAtt, Join, Output
        from troposphere import Ref, Tags, Template
        from troposphere.ec2 import PortRange, NetworkAcl, Route, \
            VPCGatewayAttachment, SubnetRouteTableAssociation, Subnet, RouteTable, \
            VPC, NetworkInterfaceProperty, NetworkAclEntry, \
            SubnetNetworkAclAssociation, EIP, Instance, InternetGateway, \
            SecurityGroupRule, SecurityGroup
        from troposphere.policies import CreationPolicy, ResourceSignal
        from troposphere.cloudformation import Init, InitFile, InitFiles, \
            InitConfig, InitService, InitServices
        from troposphere import Ref, Template, ec2, Parameter, Output, GetAtt
        from troposphere.ec2 import NetworkInterfaceProperty
        
        instance = ec2.Instance("node%s" % i)
        instance.ImageId = self.app.config.get('provision', 'aws_ec2_ami_id')
        instance.InstanceType = self.app.config.get('provision', 'aws_ec2_instance_type')
        instance.KeyName = self.app.config.get('provision', 'aws_ec2_key_name')
        instance.NetworkInterfaces = [
            NetworkInterfaceProperty(
                GroupSet=[sg,],
                AssociatePublicIpAddress='true',
                DeviceIndex='0',
                DeleteOnTermination='true',
                SubnetId=subnet
            )
        ]
        instance.UserData = Base64(
            Join(
                '',
                [
                    '#!/bin/bash -xe\n',
                    'apt update -y -q\n',
                    'apt install -y -q python3-pip\n',
                    'apt remove -y -q python3-yaml\n',
                    'pip3 install cement colorlog\n',
                    'pip3 install %s\n'%(
                        self.app.config.get('provision', 'pip_install') % self.app.config['hydra']
                    ),
                    'su -l -c "hydra client join-network --name=%s --set-default --install" ubuntu\n'%stack_name
                ])
        )
        t.add_resource(instance)
        t.add_output([
            Output(
                "ID%s" % i,
                Description="InstanceId of the newly created EC2 instance",
                Value=Ref(instance),
            ),
            Output(
                "IP%s" % i,
                Description="Public IP address of the newly created EC2 instance",
                Value=GetAtt(instance, "PublicIp"),
            ),
        ])

    def sg_subnet_vpc(self, t):
        from troposphere import Base64
        from troposphere import Ref, Tags, Template
        from troposphere.ec2 import PortRange, NetworkAcl, Route, \
            VPCGatewayAttachment, SubnetRouteTableAssociation, Subnet, RouteTable, \
            VPC, NetworkInterfaceProperty, NetworkAclEntry, \
            SubnetNetworkAclAssociation, EIP, Instance, InternetGateway, \
            SecurityGroupRule, SecurityGroup
        from troposphere.policies import CreationPolicy, ResourceSignal
        from troposphere.cloudformation import Init, InitFile, InitFiles, \
            InitConfig, InitService, InitServices
        ref_stack_id = Ref('AWS::StackId')
        ref_region = Ref('AWS::Region')
        ref_stack_name = Ref('AWS::StackName')

        if 'aws_vpc_id' in self.app.config['provision']:
            use_vpc = self.app.config['provision']['aws_vpc_id']
            use_subnet = self.app.config['provision']['aws_subnet_id']
            use_sg = self.app.config['provision']['aws_sg_id']
            self.app.log.info('Using your AWS subnet, make sure the routes and ports are configured correctly')
        else:
            VPC = t.add_resource(
                VPC(
                    'VPC',
                    CidrBlock='10.0.0.0/16',
                    Tags=Tags(
                        Application=ref_stack_id)))
            use_vpc = Ref(VPC)
            internetGateway = t.add_resource(
                InternetGateway(
                    'InternetGateway',
                    Tags=Tags(
                        Application=ref_stack_id)))

            gatewayAttachment = t.add_resource(
                VPCGatewayAttachment(
                    'AttachGateway',
                    VpcId=use_vpc,
                    InternetGatewayId=Ref(internetGateway)))

            routeTable = t.add_resource(
                RouteTable(
                    'RouteTable',
                    VpcId=use_vpc,
                    Tags=Tags(
                        Application=ref_stack_id)))

            subnet = t.add_resource(
                Subnet(
                    'Subnet',
                    CidrBlock='10.0.0.0/24',
                    VpcId=use_vpc,
                    Tags=Tags(
                        Application=ref_stack_id)))

            route = t.add_resource(
                Route(
                    'Route',
                    DependsOn='AttachGateway',
                    GatewayId=Ref('InternetGateway'),
                    DestinationCidrBlock='0.0.0.0/0',
                    RouteTableId=Ref(routeTable),
                ))

            subnetRouteTableAssociation = t.add_resource(
                SubnetRouteTableAssociation(
                    'SubnetRouteTableAssociation',
                    SubnetId=Ref(subnet),
                    RouteTableId=Ref(routeTable),
                ))

            networkAcl = t.add_resource(
                NetworkAcl(
                    'NetworkAcl',
                    VpcId=use_vpc,
                    Tags=Tags(
                        Application=ref_stack_id),
                ))

            inBoundPrivateNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'InboundHTTPNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='100',
                    Protocol='6',
                    PortRange=PortRange(To='46656', From='46656'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            inboundSSHNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'InboundSSHNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='101',
                    Protocol='6',
                    PortRange=PortRange(To='22', From='22'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            inboundResponsePortsNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'InboundResponsePortsNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='102',
                    Protocol='6',
                    PortRange=PortRange(To='65535', From='1024'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            outBoundHTTPNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'OutBoundHTTPNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='100',
                    Protocol='6',
                    PortRange=PortRange(To='80', From='80'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            
            outBoundHTTPSNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'OutBoundHTTPSNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='101',
                    Protocol='6',
                    PortRange=PortRange(To='443', From='443'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            outBoundResponsePortsNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'OutBoundResponsePortsNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='102',
                    Protocol='6',
                    PortRange=PortRange(To='65535', From='1024'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))
            outBoundLoomNetworkAclEntry = t.add_resource(
                NetworkAclEntry(
                    'OutBoundLoomNetworkAclEntry',
                    NetworkAclId=Ref(networkAcl),
                    RuleNumber='103',
                    Protocol='6',
                    PortRange=PortRange(To='46656', From='46656'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            subnetNetworkAclAssociation = t.add_resource(
                SubnetNetworkAclAssociation(
                    'SubnetNetworkAclAssociation',
                    SubnetId=Ref(subnet),
                    NetworkAclId=Ref(networkAcl),
                ))
            use_subnet = Ref(subnet)

            instanceSecurityGroup = t.add_resource(
                SecurityGroup(
                    'InstanceSecurityGroup',
                    GroupDescription='Enable SSH access via port 22',
                    SecurityGroupIngress=[
                        SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='22',
                            ToPort='22',
                            CidrIp='0.0.0.0/0'),
                        SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46657',
                            ToPort='46657',
                            CidrIp='0.0.0.0/0'),
                        SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46656',
                            ToPort='46656',
                            CidrIp='0.0.0.0/0')],
                    VpcId=use_vpc,
                ))
            use_sg = Ref(instanceSecurityGroup)

        return use_sg, use_subnet, use_vpc

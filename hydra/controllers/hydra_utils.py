from pyfiglet import Figlet
from colored import fg, attr
from datetime import datetime
import requests
import os, subprocess, boto3, json, stat, time

FIG = lambda t, f='slant': Figlet(font=f).renderText(t)
RESET = attr('reset')
ORANGE = fg('orange_1') 
SHIP = ORANGE + FIG('ShipChain') + RESET
BLUE = fg('blue')
HYDRA = BLUE + FIG('HYDRA', 'block') + RESET
from hydra.core.version import get_version

class HydraHelper(object):
    def __init__(self, app):
        self.app = app

    @classmethod
    def attach(kls, name, app):
        setattr(app, name, kls(app))
    
    @property
    def config(self):
        return self.app.config

class Utils(HydraHelper):
    def workdir(self, *extrapath):
        return os.path.realpath(os.path.join(
            self.config['hydra']['workdir'],
            *extrapath
        ))
    
    def path(self, *extrapath):
        return self.workdir(*[e % self.config['hydra'] for e in extrapath])
    
    @property
    def binary_name(self):
        return self.config['hydra']['binary_name'] % self.config['hydra']

    def binary_exec(self, path, *args):
        return self.raw_exec(path, *args)
    
    def raw_exec(self, *args):
        return subprocess.run(args, encoding='utf-8',
                stderr=subprocess.PIPE, stdout=subprocess.PIPE)

    def download_file(self, destination, url):
        import requests
        self.app.log.debug('Downloading: %s from %s'%(destination, url))
        open(destination, 'wb+').write(requests.get(url).content)
    
    def download_release_file(self, destination, file, version=None):
        host = self.config.get('hydra', 'channel_url')
        if not version:
            url = '%s/latest/%s' % (host, file)
        else:
            url = '%s/archive/%s/%s' % (host, version, file)
        return self.download_file(destination, url)
    
    def get_binary_version(self, path):
        if not os.path.exists(path):
            raise IOError('Expected shipchain binary:', path)
        return self.binary_exec(path, 'version').stderr.split('\n')[0]


class Client(HydraHelper):
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

        open('start_blockchain.sh', 'w+').write("""
        #!/bin/bash
        ./shipchain run --persistent-peers %s
        """%','.join(['tcp://%s@%s:46657'%(validator['nodekey'], ip)
        for ip, validator in remote_config['node_data'].items() ]))


        self.app.log.info('Configured!')

class Release(HydraHelper):
    def path(self, extrapath=''):
        return os.path.realpath(os.path.join(
            self.app.utils.path(self.config['release']['distdir']),
            extrapath
        ))

    @property
    def build_binary_path(self):
        return self.app.utils.path(self.config.get('release', 'build_binary_path'))

    @property
    def dist_binary_path(self):
        return os.path.join(
            self.app.utils.path(self.config.get('release', 'distdir')),
            self.app.utils.binary_name)
    
    @property
    def dist_bucket(self):
        return self.config.get('release', 'aws_s3_dist_bucket') % self.config['hydra']

    def dist_exec(self, *args):
        return self.app.utils.binary_exec(self.dist_binary_path, *args)

    def get_dist_version(self):
        return self.app.utils.get_binary_version(self.dist_binary_path)

    def get_build_version(self):
        return self.app.utils.get_binary_version(self.build_binary_path)
    
    def get_boto(self):
        return boto3.Session(profile_name=self.config.get('release', 'aws_profile'))

class Devel(HydraHelper):
    def path(self, extrapath=''):
        return os.path.realpath(os.path.join(
            self.app.utils.path(self.config['devel']['path']),
            extrapath
        ))


    def exec(self, *args):
        return self.app.utils.binary_exec(self.path('./shipchain'), *args)

    def get_dist_version(self):
        return self.app.utils.get_binary_version(self.dist_binary_path)


class Networks(HydraHelper):
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
                    'su -l -c "hydra client join-network --name=%s" ubuntu\n'%stack_name
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
                    PortRange=PortRange(To='9999', From='9999'),
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
                    PortRange=PortRange(To='9999', From='9999'),
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
                            FromPort='9999',
                            ToPort='9999',
                            CidrIp='0.0.0.0/0')],
                    VpcId=use_vpc,
                ))
            use_sg = Ref(instanceSecurityGroup)

        return use_sg, use_subnet, use_vpc

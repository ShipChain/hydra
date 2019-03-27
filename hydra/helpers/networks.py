import json
import os
import warnings

import boto3
import paramiko

from . import HydraHelper


class NetworksHelper(HydraHelper):
    def read_networks_file(self):
        try:
            return json.load(open(self.app.utils.path('networks.json'), 'r+'))
        except:  # pylint: disable=bare-except
            return {}

    def register(self, network_name, options):
        networks = self.read_networks_file()

        networks[network_name] = options or {}

        json.dump(networks, open(self.app.utils.path('networks.json'), 'w+'))

    def deregister(self, network_name):
        networks = self.read_networks_file()

        if network_name in networks:
            networks.pop(network_name, '')
            self.app.log.info(f'Deregistering network: {network_name}')

        json.dump(networks, open(self.app.utils.path('networks.json'), 'w+'))

    def run_command(self, ip, cmd):
        default_key = '~/.ssh/%(aws_ec2_key_name)s.pem'
        provision = self.app.config['provision']
        key = os.path.expanduser(
            'aws_ec2_key_path' in provision and
            provision['aws_ec2_key_path'] % provision or
            default_key % provision
        )

        self.app.log.info(f'Running on {ip}: {cmd}')
        self.app.log.debug(f'Using keyfile: {key}')

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)

            client.connect(ip, username='ubuntu', key_filename=key)

            _, stdout, stderr = client.exec_command(cmd)
            output = ''.join(line for line in stdout)
            error = ''.join(line for line in stderr)

            client.close()

            self.app.log.debug(f'Output: {output}')
            self.app.log.error(f'Error: {error}')
            return output

    def bootstrap_config(self, network_name):
        networks = self.read_networks_file()
        network = networks[network_name]
        folder = f'networks/{network_name}'

        def open_first_file(file_name):
            ip = network['ips'][0]
            output = self.run_command(ip, f'cat {network_name}/{file_name}')
            return output

        peers = [(ip, validator['pubkey'], validator['nodekey'])
                 for ip, validator in network['node_data'].items()]

        os.makedirs(f'networks/{network_name}/chaindata/config/', exist_ok=True)

        cd_genesis = json.loads(open_first_file('chaindata/config/genesis.json'))
        cd_genesis['genesis_time'] = '1970-01-01T00:00:00Z'
        cd_genesis['validators'] = [
            {
                "name": "",
                "power": '1000',
                "pub_key": {
                    "type": "tendermint/PubKeyEd25519",
                    "value": pubkey
                }
            }
            for ip, pubkey, nodekey in peers
        ]

        json.dump(cd_genesis, open(f'{folder}/chaindata/config/genesis.json', 'w+'), indent=4)

        # GENESIS.json
        genesis = json.loads(open_first_file('genesis.json'))

        for contact_num, contract in enumerate(genesis['contracts']):
            if contract['name'] == 'dpos':
                genesis['contracts'][contact_num]['init']['params']['witnessCount'] = '51'
                genesis['contracts'][contact_num]['init']['validators'] = [
                    {'pubKey': pubkey, 'power': '1000'}
                    for ip, pubkey, nodekey in peers
                ]
        json.dump(genesis, open(f'{folder}/genesis.json', 'w+'), indent=4)

    def get_boto(self):
        return boto3.Session(profile_name=self.config.get('provision', 'aws_profile'))

    # pylint: disable=too-many-arguments
    def add_instance(self, stack_name, template, instance_num, security_group, subnet):
        from troposphere import Base64, Join, Ref, ec2, Output, GetAtt

        instance = ec2.Instance(f'node{instance_num}')
        instance.ImageId = self.app.config.get('provision', 'aws_ec2_ami_id')
        instance.InstanceType = self.app.config.get('provision', 'aws_ec2_instance_type')
        instance.KeyName = self.app.config.get('provision', 'aws_ec2_key_name')
        instance.NetworkInterfaces = [
            ec2.NetworkInterfaceProperty(
                GroupSet=[security_group, ],
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
                    'apt install -y -q htop tmux zsh jq || true\n',
                    'apt remove -y -q python3-yaml\n',
                    'pip3 install cement colorlog\n',
                    f'pip3 install {self.app.config.get("provision", "pip_install") % self.app.config["hydra"]}\n',
                    f'su -l -c "hydra client join-network --name={stack_name} --set-default --install" ubuntu\n'
                ])
        )
        template.add_resource(instance)
        template.add_output([
            Output(
                f"IP{instance_num}",
                Description="InstanceId of the newly created EC2 instance",
                Value=Ref(instance),
            ),
            Output(
                f"IP{instance_num}",
                Description="Public IP address of the newly created EC2 instance",
                Value=GetAtt(instance, "PublicIp"),
            ),
        ])

    def sg_subnet_vpc(self, template):
        from troposphere import Ref, Tags, ec2

        ref_stack_id = Ref('AWS::StackId')

        if 'aws_vpc_id' in self.app.config['provision']:
            use_subnet = self.app.config['provision']['aws_subnet_id']
            use_sg = self.app.config['provision']['aws_sg_id']
            self.app.log.info('Using your AWS subnet, make sure the routes and ports are configured correctly')
        else:
            vpc = Ref(template.add_resource(
                ec2.VPC(
                    'VPC',
                    CidrBlock='10.0.0.0/16',
                    Tags=Tags(
                        Application=ref_stack_id))))

            internet_gateway = template.add_resource(
                ec2.InternetGateway(
                    'InternetGateway',
                    Tags=Tags(
                        Application=ref_stack_id)))

            template.add_resource(
                ec2.VPCGatewayAttachment(
                    'AttachGateway',
                    VpcId=vpc,
                    InternetGatewayId=Ref(internet_gateway)))

            route_table = template.add_resource(
                ec2.RouteTable(
                    'RouteTable',
                    VpcId=vpc,
                    Tags=Tags(
                        Application=ref_stack_id)))

            subnet = template.add_resource(
                ec2.Subnet(
                    'Subnet',
                    CidrBlock='10.0.0.0/24',
                    VpcId=vpc,
                    Tags=Tags(
                        Application=ref_stack_id)))

            template.add_resource(
                ec2.Route(
                    'Route',
                    DependsOn='AttachGateway',
                    GatewayId=Ref('InternetGateway'),
                    DestinationCidrBlock='0.0.0.0/0',
                    RouteTableId=Ref(route_table),
                ))

            template.add_resource(
                ec2.SubnetRouteTableAssociation(
                    'SubnetRouteTableAssociation',
                    SubnetId=Ref(subnet),
                    RouteTableId=Ref(route_table),
                ))

            network_acl = template.add_resource(
                ec2.NetworkAcl(
                    'NetworkAcl',
                    VpcId=vpc,
                    Tags=Tags(
                        Application=ref_stack_id),
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundHTTPNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='100',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='46656', From='46656'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundSSHNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='101',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='22', From='22'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundResponsePortsNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='102',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='65535', From='1024'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'OutBoundHTTPNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='100',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='80', From='80'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'OutBoundHTTPSNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='101',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='443', From='443'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'OutBoundResponsePortsNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='102',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='65535', From='1024'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))
            template.add_resource(
                ec2.NetworkAclEntry(
                    'OutBoundLoomNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='103',
                    Protocol='6',
                    PortRange=ec2.PortRange(To='46656', From='46656'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.SubnetNetworkAclAssociation(
                    'SubnetNetworkAclAssociation',
                    SubnetId=Ref(subnet),
                    NetworkAclId=Ref(network_acl),
                ))
            use_subnet = Ref(subnet)

            instance_security_group = template.add_resource(
                ec2.SecurityGroup(
                    'InstanceSecurityGroup',
                    GroupDescription='Enable SSH access via port 22',
                    SecurityGroupIngress=[
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='22',
                            ToPort='22',
                            CidrIp='0.0.0.0/0'),
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46657',
                            ToPort='46657',
                            CidrIp='0.0.0.0/0'),
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46656',
                            ToPort='46656',
                            CidrIp='0.0.0.0/0')],
                    VpcId=vpc,
                ))
            use_sg = Ref(instance_security_group)

        return use_sg, use_subnet

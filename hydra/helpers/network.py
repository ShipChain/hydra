import json
import os
import warnings

import boto3
import paramiko
from troposphere import Base64, Join, Output, Select, GetAtt, GetAZs, Ref, Tags
from troposphere import ec2, iam, route53, elasticloadbalancingv2 as elb

import yaml

from . import HydraHelper


class NetworkHelper(HydraHelper):
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
            if error:
                self.app.log.error(f'Error: {error}')
            return output

    def scp(self, ip, file, dest):
        default_key = '~/.ssh/%(aws_ec2_key_name)s.pem'
        provision = self.app.config['provision']
        key = os.path.expanduser(
            'aws_ec2_key_path' in provision and
            provision['aws_ec2_key_path'] % provision or
            default_key % provision
        )

        self.app.log.info(f'Copying to {ip}: {file}')
        self.app.log.debug(f'Using keyfile: {key}')

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)

            client.connect(ip, username='ubuntu', key_filename=key)

            ftp_client = client.open_sftp()
            ftp_client.put(file, dest)
            ftp_client.close()

            client.close()

    def bootstrap_config(self, network_name):
        networks = self.read_networks_file()
        network = networks[network_name]
        folder = f'networks/{network_name}'

        def open_nth_file(file_name, n=0):
            ip = network['ips'][n]
            output = self.run_command(ip, f'cat {network_name}/{file_name}')
            return output

        peers = [(ip, validator['pubkey'], validator['nodekey'])
                 for ip, validator in network['node_data'].items()]

        os.makedirs(f'networks/{network_name}/chaindata/config/', exist_ok=True)

        cd_genesis = json.loads(open_nth_file('chaindata/config/genesis.json'))
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
        genesis = json.loads(open_nth_file('genesis.json'))
        oracle_addrs = [open_nth_file('node_addr.b64', n) for n in range(0, len(network['ips']))]

        for contract_num, contract in enumerate(genesis['contracts']):
            if contract['name'] == 'dposV3':
                genesis['contracts'][contract_num]['init']['params']['validatorCount'] = str(
                    self.app.config['provision']['dpos']['validator_count'])
                genesis['contracts'][contract_num]['init']['params']['electionCycleLength'] = str(
                    self.app.config['provision']['dpos']['election_cycle_length'])
                genesis['contracts'][contract_num]['init']['validators'] = [
                    {'pubKey': pubkey, 'power': '1000'}
                    for ip, pubkey, nodekey in peers
                ]
                genesis['contracts'][contract_num]['init']['params']['oracleAddress'] = {
                    "chain_id": "default",
                    "local": oracle_addrs[0],
                }
            elif contract['name'] == 'chainconfig':
                genesis['contracts'][contract_num]['init']['features'] = [
                    {
                        "name": "auth:sigtx:eth",
                        "status": "WAITING"
                    },
                    {
                        "name": "auth:sigtx:default",
                        "status": "WAITING"
                    },
                    {
                        "name": "tg:check-txhash",
                        "status": "WAITING"
                    }
                ]
            elif 'gateway' in contract['name']:
                genesis['contracts'][contract_num]['init'] = {
                    "owner": {
                        "chain_id": "default",
                        "local": oracle_addrs[0],
                    },
                    "oracles": [
                        {
                            "chain_id": "default",
                            "local": oracle_addr,
                        } for oracle_addr in oracle_addrs
                    ],
                    "first_mainnet_block_num": str(self.app.config['provision']['gateway']['first_mainnet_block_num'])
                }

        json.dump(genesis, open(f'{folder}/genesis.json', 'w+'), indent=4)

        # LOOM.YAML
        loom_config = {
            'ChainID': 'default',
            'RegistryVersion': 2,
            'DPOSVersion': 3,
            'ReceiptsVersion': 2,
            'LoomLogLevel': self.app.config['loom']['loom_log_level'],
            'ContractLogLevel': self.app.config['loom']['contract_log_level'],
            'BlockchainLogLevel': self.app.config['loom']['blockchain_log_level'],
            'EVMAccountsEnabled': True,
            'TransferGateway': {
                'ContractEnabled': True
            },
            'LoomCoinTransferGateway': {
                'ContractEnabled': True
            },
            'ChainConfig': {
                'ContractEnabled': True
            },
            'Auth': {
                'Chains': {
                    'default': {
                        'TxType': 'loom'
                    },
                    'eth': {
                        'TxType': 'eth',
                        'AccountType': 1
                    }
                }
            }
        }

        for gateway in ('TransferGateway', 'LoomCoinTransferGateway'):
            loom_config[gateway]['ContractEnabled'] = True
            loom_config[gateway]['OracleEnabled'] = False
            loom_config[gateway]['EthereumURI'] = self.app.config['provision']['gateway']['ethereum_uri']

            loom_config[gateway]['MainnetPrivateKeyPath'] = 'oracle_eth_priv.key'
            loom_config[gateway]['MainnetPollInterval'] = self.app.config['provision']['gateway']['mainnet_poll_interval']

            loom_config[gateway]['DAppChainPrivateKeyPath'] = 'node_priv.key'
            loom_config[gateway]['DAppChainReadURI'] = 'http://localhost:46658/query'
            loom_config[gateway]['DAppChainWriteURI'] = 'http://localhost:46658/rpc'
            loom_config[gateway]['DAppChainEventsURI'] = 'ws://localhost:46658/queryws'
            loom_config[gateway]['DAppChainPollInterval'] = self.app.config['provision']['gateway'][
                'dappchain_poll_interval']

            loom_config[gateway]['OracleLogLevel'] = self.app.config['provision']['gateway']['oracle_log_level']
            loom_config[gateway]['OracleLogDestination'] = f'file://{gateway}-oracle.log'
            loom_config[gateway]['OracleStartupDelay'] = self.app.config['provision']['gateway']['oracle_startup_delay']
            loom_config[gateway]['OracleReconnectInterval'] = self.app.config['provision']['gateway'][
                'oracle_reconnect_interval']

        loom_config['TransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_tg_contract_hex_address']
        loom_config['LoomCoinTransferGateway']['MainnetContractHexAddress'] = self.app.config['provision']['gateway'][
            'mainnet_lctg_contract_hex_address']

        open(f'{folder}/loom.yaml', 'w+').write(
            yaml.dump(loom_config, indent=4, default_flow_style=False))

    def get_boto(self):
        return boto3.Session(profile_name=self.config.get('provision', 'aws_profile'))

    def add_instance_profile(self, stack_name, template, provision_refs):
        role = template.add_resource(
            iam.Role(
                "Role",
                RoleName=f'{stack_name}-role',
                Policies=[
                    iam.Policy(
                        PolicyName=f'{stack_name}-s3-policy',
                        PolicyDocument={
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Sid": "Troposphere0",
                                    "Effect": "Allow",
                                    "Action": [
                                        "s3:PutAccountPublicAccessBlock",
                                        "s3:GetAccountPublicAccessBlock",
                                        "s3:ListAllMyBuckets",
                                        "s3:HeadBucket"
                                    ],
                                    "Resource": "*"
                                },
                                {
                                    "Sid": "Troposphere1",
                                    "Effect": "Allow",
                                    "Action": "s3:*",
                                    "Resource": [
                                        "arn:aws:s3:::shipchain-network-dist",
                                        f"arn:aws:s3:::shipchain-network-dist/jumpstart/{stack_name}/*"
                                    ]
                                }
                            ]
                        }
                    )
                ],
                AssumeRolePolicyDocument={
                    "Version": "2008-10-17",
                    "Statement": [
                        {
                            "Action": [
                                "sts:AssumeRole"
                            ],
                            "Effect": "Allow",
                            "Principal":
                                {
                                    "Service": [
                                        "ec2.amazonaws.com"
                                    ]
                                }
                        }
                    ]
                }
            )
        )

        provision_refs.instance_profile = template.add_resource(
            iam.InstanceProfile(
                "InstanceProfile",
                Roles=[Ref(role)]
            )
        )

    def add_instance(self, stack_name, template, provision_refs, instance_num, version=None):
        instance = ec2.Instance(f'node{instance_num}')
        instance.IamInstanceProfile = Ref(provision_refs.instance_profile)
        instance.ImageId = self.app.config.get('provision', 'aws_ec2_ami_id')
        instance.InstanceType = self.app.config.get('provision', 'aws_ec2_instance_type')
        instance.KeyName = self.app.config.get('provision', 'aws_ec2_key_name')
        instance.NetworkInterfaces = [
            ec2.NetworkInterfaceProperty(
                GroupSet=[provision_refs.security_group_ec2, ],
                AssociatePublicIpAddress='true',
                DeviceIndex='0',
                DeleteOnTermination='true',
                SubnetId=provision_refs.random_subnet
            )
        ]
        version_flag = f' --version={version}' if version else ''
        join_network_arguments = f'--name={stack_name}{version_flag} --set-default --install --no-configure'

        instance.UserData = Base64(
            Join(
                '',
                [
                    '#!/bin/bash -xe\n',
                    'apt update -y -q\n',
                    'UCF_FORCE_CONFOLD=1 DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" -qq -y install python3-pip\n',
                    'apt install -y -q htop tmux zsh jq || true\n',
                    'apt remove -y -q python3-yaml\n',
                    'pip3 install cement colorlog\n',
                    f'pip3 install {self.app.config.get("provision", "pip_install") % self.app.config["hydra"]}\n',
                    f'su -l -c "hydra client join-network {join_network_arguments}" ubuntu\n'
                ])
        )
        template.add_resource(instance)
        template.add_output([
            Output(
                f"ID{instance_num}",
                Description="InstanceId of the newly created EC2 instance",
                Value=Ref(instance),
            ),
            Output(
                f"IP{instance_num}",
                Description="Public IP address of the newly created EC2 instance",
                Value=GetAtt(instance, "PublicIp"),
            ),
        ])
        return instance

    def add_alb(self, template, provision_refs, instances):

        alb = template.add_resource(elb.LoadBalancer(
            'ALB',
            LoadBalancerAttributes=[elb.LoadBalancerAttributes(Key='idle_timeout.timeout_seconds', Value='3600')],
            Subnets=provision_refs.subnets,
            Type='application',
            Scheme='internet-facing',
            IpAddressType='ipv4',
            SecurityGroups=[provision_refs.security_group_alb]
        ))

        default_target_group = template.add_resource(elb.TargetGroup(
            'DefaultTargetGroup',
            Port=46658,
            Protocol='HTTP',
            Targets=[elb.TargetDescription(Id=Ref(instance)) for instance in instances],
            HealthCheckProtocol='HTTP',
            HealthCheckPath='/rpc',
            TargetGroupAttributes=[
                elb.TargetGroupAttribute(Key='stickiness.enabled', Value='true'),
                elb.TargetGroupAttribute(Key='stickiness.type', Value='lb_cookie'),
                elb.TargetGroupAttribute(Key='stickiness.lb_cookie.duration_seconds', Value='86400'),
            ],
            VpcId=provision_refs.vpc
        ))

        template.add_resource(elb.Listener(
            'ALBListener',
            DefaultActions=[elb.Action(
                'DefaultAction',
                Type='forward',
                TargetGroupArn=Ref(default_target_group)
            )],
            LoadBalancerArn=Ref(alb),
            Port=46658,
            Protocol='HTTPS',
            SslPolicy='ELBSecurityPolicy-TLS-1-2-2017-01',
            Certificates=[
                elb.Certificate(CertificateArn='arn:aws:acm:us-east-1:489745816517:certificate/'
                                               'fbb68210-264e-4340-9c5c-a7687f993579')
            ]
        ))

        provision_refs.alb = alb

    def add_route53(self, stack_name, template, provision_refs):
        template.add_resource(route53.RecordSetType(
            "NetworkDNSRecord",
            HostedZoneName="network.shipchain.io.",
            Comment=f"DNS name for {stack_name} network ALB",
            Name=f"{stack_name}.network.shipchain.io.",
            Type="A",
            AliasTarget=route53.AliasTarget(DNSName=GetAtt(provision_refs.alb, 'DNSName'),
                                            HostedZoneId=GetAtt(provision_refs.alb, "CanonicalHostedZoneID"))
        ))

    def sg_subnet_vpc(self, template, provision_refs):
        ref_stack_id = Ref('AWS::StackId')

        if 'aws_vpc_id' in self.app.config['provision']:
            vpc = self.app.config['provision']['aws_vpc_id']
            use_subnet = self.app.config['provision']['aws_subnet_id']
            use_subnet2 = self.app.config['provision']['aws_subnet2_id']
            use_sg = self.app.config['provision']['aws_sg_id']
            use_alb_sg = self.app.config['provision']['alb_sg_id']
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
                    AvailabilityZone=Select(0, GetAZs("")),
                    Tags=Tags(
                        Application=ref_stack_id)))

            subnet2 = template.add_resource(
                ec2.Subnet(
                    'Subnet2',
                    CidrBlock='10.0.1.0/24',
                    VpcId=vpc,
                    AvailabilityZone=Select(1, GetAZs("")),
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

            template.add_resource(
                ec2.SubnetRouteTableAssociation(
                    'Subnet2RouteTableAssociation',
                    SubnetId=Ref(subnet2),
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
                    'InboundSSHNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='100',
                    Protocol='6',
                    PortRange=ec2.PortRange(From='22', To='22'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundResponsePortsNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='101',
                    Protocol='6',
                    PortRange=ec2.PortRange(From='1024', To='65535'),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundICMPNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='102',
                    Protocol='1',
                    Icmp=ec2.ICMP(Code=-1, Type=-1),
                    Egress='false',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            # Only used when Blockscout is deployed
            template.add_resource(
                ec2.NetworkAclEntry(
                    'InboundHttpsNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='103',
                    Protocol='6',
                    PortRange=ec2.PortRange(From='443', To='443'),
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
                    PortRange=ec2.PortRange(From='80', To='80'),
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
                    PortRange=ec2.PortRange(From='443', To='443'),
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
                    PortRange=ec2.PortRange(From='1024', To='65535'),
                    Egress='true',
                    RuleAction='allow',
                    CidrBlock='0.0.0.0/0',
                ))

            template.add_resource(
                ec2.NetworkAclEntry(
                    'OutboundICMPNetworkAclEntry',
                    NetworkAclId=Ref(network_acl),
                    RuleNumber='103',
                    Protocol='1',
                    Icmp=ec2.ICMP(Code=-1, Type=-1),
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
            template.add_resource(
                ec2.SubnetNetworkAclAssociation(
                    'Subnet2NetworkAclAssociation',
                    SubnetId=Ref(subnet2),
                    NetworkAclId=Ref(network_acl),
                ))
            use_subnet = Ref(subnet)
            use_subnet2 = Ref(subnet2)

            alb_security_group = template.add_resource(
                ec2.SecurityGroup(
                    'ALBSecurityGroup',
                    GroupDescription='ALB allows traffic from public, is used to terminate SSL',
                    SecurityGroupIngress=[
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46658',
                            ToPort='46658',
                            CidrIp='0.0.0.0/0'),
                    ],
                    VpcId=vpc,
                )
            )
            use_alb_sg = Ref(alb_security_group)

            instance_security_group = template.add_resource(
                ec2.SecurityGroup(
                    'InstanceSecurityGroup',
                    GroupDescription='Enable tendermint and SSH for all nodes',
                    SecurityGroupIngress=[
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='22',
                            ToPort='22',
                            CidrIp='0.0.0.0/0'),
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46656',
                            ToPort='46656',
                            CidrIp='0.0.0.0/0'),
                        ec2.SecurityGroupRule(
                            IpProtocol='tcp',
                            FromPort='46658',
                            ToPort='46658',
                            CidrIp='0.0.0.0/0'),
                        ec2.SecurityGroupRule(
                            IpProtocol='icmp',
                            FromPort='-1',
                            ToPort='-1',
                            CidrIp='0.0.0.0/0'),
                    ],
                    VpcId=vpc,
                ))
            use_sg = Ref(instance_security_group)

        provision_refs.vpc = vpc
        provision_refs.security_group_ec2 = use_sg
        provision_refs.security_group_alb = use_alb_sg
        provision_refs.subnets.append(use_subnet)
        provision_refs.subnets.append(use_subnet2)

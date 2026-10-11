from __future__ import annotations
import asyncio
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from .base import CloudError, option, until

class AwsProvider:
    kind='aws'
    def __init__(self,credentials,client_factory=None):
        self.credentials=credentials;self.clients={};self.factory=client_factory or boto3.client
    def client(self,service,region):
        key=(service,region)
        if key not in self.clients:
            self.clients[key]=self.factory(service,region_name=region,aws_access_key_id=self.credentials['access_key_id'],aws_secret_access_key=self.credentials['secret_access_key'],aws_session_token=self.credentials.get('session_token'),config=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':0}))
        return self.clients[key]
    async def close(self):
        for client in self.clients.values():client.close()
    async def call(self,region,method,*,service='ec2',missing=False,**params):
        try:return await asyncio.to_thread(getattr(self.client(service,region),method),**params)
        except ClientError as exc:
            code=exc.response['Error']['Code']
            if missing and code in {'InvalidInstanceID.NotFound','InvalidVpcID.NotFound','InvalidSubnetID.NotFound','InvalidGroup.NotFound','InvalidInternetGatewayID.NotFound','InvalidRouteTableID.NotFound','InvalidVolume.NotFound'}:return None
            raise CloudError('AWS rejected '+method+' ('+code+'). Check IAM permissions, quota and configuration.',definitive=exc.response.get('ResponseMetadata',{}).get('HTTPStatusCode',500)<500 and code not in {'RequestLimitExceeded','Throttling','RequestTimeout'}) from None
        except BotoCoreError:raise CloudError('AWS response was lost; reconcile resources before retrying.') from None
    async def identity(self):
        value=await self.call('us-east-1','get_caller_identity',service='sts')
        return {'account':value['Account'],'principal':value['Arn']}
    async def pages(self,region,method,key,**params):
        rows=[]
        while True:
            value=await self.call(region,method,**params);rows+=value.get(key,[])
            if not value.get('NextToken'):return rows
            params['NextToken']=value['NextToken']
    async def catalog(self,region='',zone=''):
        regions=await self.call(region or 'us-east-1','describe_regions',AllRegions=False)
        result={'regions':[option(r['RegionName']) for r in regions['Regions']]}
        if not region:return result
        zones=await self.call(region,'describe_availability_zones',Filters=[{'Name':'state','Values':['available']}])
        types=await self.pages(region,'describe_instance_types','InstanceTypes',Filters=[{'Name':'processor-info.supported-architecture','Values':['x86_64']},{'Name':'hypervisor','Values':['nitro']}])
        if zone:
            offerings=await self.pages(region,'describe_instance_type_offerings','InstanceTypeOfferings',LocationType='availability-zone',Filters=[{'Name':'location','Values':[zone]}])
            offered={entry['InstanceType'] for entry in offerings};types=[entry for entry in types if entry['InstanceType'] in offered]
        images=await self.call(region,'describe_images',Owners=['099720109477'],Filters=[{'Name':'name','Values':['ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*']},{'Name':'state','Values':['available']}])
        vpcs=await self.pages(region,'describe_vpcs','Vpcs');subnets=await self.pages(region,'describe_subnets','Subnets')
        result.update(zones=[option(z['ZoneName']) for z in zones['AvailabilityZones']],sizes=[option(t['InstanceType'],f"{t['InstanceType']} · {t['VCpuInfo']['DefaultVCpus']} vCPU · {t['MemoryInfo']['SizeInMiB']//1024} GiB") for t in types],images=[option(i['ImageId'],i['Name']) for i in sorted(images['Images'],key=lambda i:i['CreationDate'],reverse=True)[:5]],networks=[option(v['VpcId'],v['VpcId']+' · '+v['CidrBlock']) for v in vpcs],subnets=[option(s['SubnetId'],s['SubnetId']+' · '+s['CidrBlock'],network=s['VpcId'],zone=s['AvailabilityZone']) for s in subnets],volume_types=[option('gp3','General purpose SSD'),option('gp2','General purpose SSD (gp2)')])
        return result
    def tags(self,d,name):return [{'Key':'termx-deployment','Value':d['id']},{'Key':'Name','Value':name}]
    async def find(self,kind,name,d):
        mapping={'vpc':('describe_vpcs','Vpcs','VpcId'),'subnet':('describe_subnets','Subnets','SubnetId'),'igw':('describe_internet_gateways','InternetGateways','InternetGatewayId'),'route':('describe_route_tables','RouteTables','RouteTableId'),'sg':('describe_security_groups','SecurityGroups','GroupId'),'vm':('describe_instances','Reservations','InstanceId'),'disk':('describe_volumes','Volumes','VolumeId')}
        method,key,idkey=mapping[kind]
        value=await self.call(d['plan']['region'],method,Filters=[{'Name':'tag:termx-deployment','Values':[d['id']]},{'Name':'tag:Name','Values':[name]}])
        rows=value[key]
        if kind=='vm':rows=[i for r in rows for i in r['Instances'] if i['State']['Name']!='terminated']
        if len(rows)>1:raise CloudError('Multiple cloud resources match this operation; cleanup requires inspection.')
        return {'id':rows[0][idkey]} if rows else None
    async def create(self,kind,name,params,d):
        region=d['plan']['region'];tags=self.tags(d,name)
        mapping={'vpc':('create_vpc','Vpc','VpcId','vpc'),'subnet':('create_subnet','Subnet','SubnetId','subnet'),'igw':('create_internet_gateway','InternetGateway','InternetGatewayId','internet-gateway'),'route':('create_route_table','RouteTable','RouteTableId','route-table'),'sg':('create_security_group',None,'GroupId','security-group')}
        if kind=='vm':
            result=await self.call(region,'run_instances',**params,ClientToken=d['id'],MinCount=1,MaxCount=1,TagSpecifications=[{'ResourceType':'instance','Tags':tags},{'ResourceType':'volume','Tags':tags}])
            return {'id':result['Instances'][0]['InstanceId']}
        method,key,idkey,resource=mapping[kind]
        result=await self.call(region,method,**params,TagSpecifications=[{'ResourceType':resource,'Tags':tags}])
        return {'id':(result[key] if key else result)[idkey]}
    async def provision(self,d,ensure,userdata):
        p=d['plan'];region=p['region'];name='termx-'+d['id'][:20]
        if p['network_mode']=='managed':
            vpc=(await ensure('vpc',name,{'CidrBlock':p['network_cidr']}))['id']
            async def vpc_available():
                result=await self.call(region,'describe_vpcs',VpcIds=[vpc],missing=True)
                return bool(result and result['Vpcs'] and result['Vpcs'][0]['State']=='available')
            await until(vpc_available)
            subnet=(await ensure('subnet',name,{'VpcId':vpc,'CidrBlock':p['subnet_cidr'],'AvailabilityZone':p['zone']}))['id']
            gateway=(await ensure('igw',name,{}))['id']
            details=await self.call(region,'describe_internet_gateways',InternetGatewayIds=[gateway])
            if not details['InternetGateways'][0]['Attachments']:await self.call(region,'attach_internet_gateway',InternetGatewayId=gateway,VpcId=vpc)
            route=(await ensure('route',name,{'VpcId':vpc}))['id']
            details=(await self.call(region,'describe_route_tables',RouteTableIds=[route]))['RouteTables'][0]
            if not any(r.get('DestinationCidrBlock')=='0.0.0.0/0' for r in details['Routes']):await self.call(region,'create_route',RouteTableId=route,DestinationCidrBlock='0.0.0.0/0',GatewayId=gateway)
            if not any(a.get('SubnetId')==subnet for a in details['Associations']):await self.call(region,'associate_route_table',RouteTableId=route,SubnetId=subnet)
        else:vpc=p['network'];subnet=p['subnet']
        group=(await ensure('sg',name,{'GroupName':name,'Description':'TermX runner SSH','VpcId':vpc}))['id']
        details=(await self.call(region,'describe_security_groups',GroupIds=[group]))['SecurityGroups'][0]
        if not details['IpPermissions']:await self.call(region,'authorize_security_group_ingress',GroupId=group,IpPermissions=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':p['ssh_cidr']}]}])
        image=(await self.call(region,'describe_images',ImageIds=[p['image']]))['Images'][0]
        volumes=[{'DeviceName':image['RootDeviceName'],'Ebs':{'VolumeSize':p['boot']['size_gb'],'VolumeType':p['boot']['type'],'DeleteOnTermination':True,'Encrypted':True}}]
        volumes += [{'DeviceName':'/dev/sd'+chr(102+i),'Ebs':{'VolumeSize':v['size_gb'],'VolumeType':v['type'],'DeleteOnTermination':True,'Encrypted':True}} for i,v in enumerate(p['volumes'])]
        return await ensure('vm',name,{'ImageId':p['image'],'InstanceType':p['size'],'Placement':{'AvailabilityZone':p['zone']},'NetworkInterfaces':[{'DeviceIndex':0,'SubnetId':subnet,'Groups':[group],'AssociatePublicIpAddress':p['public_ip']}],'BlockDeviceMappings':volumes,'UserData':userdata,'MetadataOptions':{'HttpTokens':'required','HttpEndpoint':'enabled'}})
    async def machine(self,d):
        vm=next(r for r in d['resources'] if r['kind']=='vm' and r.get('id'))
        value=await self.call(d['plan']['region'],'describe_instances',InstanceIds=[vm['id']])
        instance=value['Reservations'][0]['Instances'][0]
        if instance['State']['Name']!='running':return None
        return {'host':instance.get('PublicIpAddress') if d['plan']['public_ip'] else instance.get('PrivateIpAddress'),'instance_id':vm['id']}
    async def delete(self,resource,d):
        region=d['plan']['region'];kind=resource['kind'];identifier=resource['id']
        if kind=='disk':
            async def read():
                value=await self.call(region,'describe_volumes',VolumeIds=[identifier],missing=True)
                return value['Volumes'][0] if value and value.get('Volumes') else None
            row=await read()
            if row is None:return
            if {t['Key']:t['Value'] for t in row.get('Tags',[])}.get('termx-deployment')!=d['id']:raise CloudError('AWS disk is not owned by this deployment.')
            await until(read,lambda row:row is None or row['State']=='available')
            if await read():await self.call(region,'delete_volume',VolumeId=identifier,missing=True)
            await until(read,lambda row:row is None)
            return
        lookup={'vm':('describe_instances','InstanceIds','Reservations'),'vpc':('describe_vpcs','VpcIds','Vpcs'),'subnet':('describe_subnets','SubnetIds','Subnets'),'igw':('describe_internet_gateways','InternetGatewayIds','InternetGateways'),'route':('describe_route_tables','RouteTableIds','RouteTables'),'sg':('describe_security_groups','GroupIds','SecurityGroups')}
        async def read_resource():
            method,arg,key=lookup[kind]
            value=await self.call(region,method,**{arg:[identifier]},missing=True)
            rows=value.get(key,[]) if value else []
            if kind=='vm':rows=[i for r in rows for i in r['Instances'] if i['State']['Name']!='terminated']
            if not rows:return None
            row=rows[0]
            if {t['Key']:t['Value'] for t in row.get('Tags',[])}.get('termx-deployment')!=d['id']:raise CloudError('AWS resource ownership changed; refusing cleanup.')
            return row
        if not await read_resource():return
        if kind=='vm':
            await self.call(region,'terminate_instances',InstanceIds=[identifier],missing=True)
        elif kind=='route':
            row=(await self.call(region,'describe_route_tables',RouteTableIds=[identifier]))['RouteTables'][0]
            for association in row['Associations']:
                if not association.get('Main'):await self.call(region,'disassociate_route_table',AssociationId=association['RouteTableAssociationId'])
            await self.call(region,'delete_route_table',RouteTableId=identifier,missing=True)
        elif kind=='igw':
            row=(await self.call(region,'describe_internet_gateways',InternetGatewayIds=[identifier]))['InternetGateways'][0]
            for attachment in row['Attachments']:await self.call(region,'detach_internet_gateway',InternetGatewayId=identifier,VpcId=attachment['VpcId'])
            await self.call(region,'delete_internet_gateway',InternetGatewayId=identifier,missing=True)
        else:
            method,arg={'sg':('delete_security_group','GroupId'),'subnet':('delete_subnet','SubnetId'),'vpc':('delete_vpc','VpcId'),'disk':('delete_volume','VolumeId')}[kind]
            await self.call(region,method,**{arg:identifier},missing=True)
        await until(read_resource,lambda value:value is None)
    async def leftovers(self,d):
        # RunInstances creates owned disks atomically; reconcile them even if its response was lost.
        values=await self.call(d['plan']['region'],'describe_volumes',Filters=[{'Name':'tag:termx-deployment','Values':[d['id']]}])
        return [{'kind':'disk','id':r['VolumeId'],'name':r['VolumeId']} for r in values['Volumes']]

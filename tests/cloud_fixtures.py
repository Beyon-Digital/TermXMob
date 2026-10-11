"""Stateful mock cloud APIs: resource creation, borrowed networks and deletion.

The production adapters and durable coordinator are used unchanged. AWS request
shapes are checked with botocore's service model; Azure/GCP use HTTP transports.
"""
import copy
import json
from urllib.parse import urlsplit, unquote
import httpx
from botocore.session import get_session
from botocore.validate import validate_parameters
from botocore.exceptions import ClientError
from termx.runners.cloud.aws import AwsProvider
from termx.runners.cloud.azure import AzureProvider
from termx.runners.cloud.gcp import GcpProvider

CREDENTIALS={
 'aws':{'access_key_id':'AKIAFIXTURE0000000000','secret_access_key':'fixture-secret-only'},
 'azure':{'tenant_id':'11111111-1111-1111-1111-111111111111','client_id':'22222222-2222-2222-2222-222222222222','subscription_id':'33333333-3333-3333-3333-333333333333','client_secret':'fixture-secret-only'},
 'gcp':{'project_id':'fixture-project','client_email':'runner@fixture-project.iam.gserviceaccount.com','private_key':'fixture-unused-with-preauthenticated-transport'},
}

class AwsCloud:
    def __init__(self):self.rows={};self.calls=[];self.fail=None;self.model=get_session().get_service_model('ec2')
    def factory(self,kind,credentials):return AwsProvider(credentials,client_factory=lambda service,**kwargs:self)
    def close(self):pass
    def __getattr__(self,method):
        def invoke(**params):
            if method!='get_caller_identity':
                api=''.join(word.capitalize() for word in method.split('_'))
                validate_parameters(params,self.model.operation_model(api).input_shape)
            self.calls.append((method,copy.deepcopy(params)))
            if self.fail==method:raise ClientError({'Error':{'Code':'UnauthorizedOperation'},'ResponseMetadata':{'HTTPStatusCode':403}},method)
            if method=='get_caller_identity':return {'Account':'123456789012','Arn':'arn:aws:iam::123456789012:user/fixture'}
            if method=='describe_regions':return {'Regions':[{'RegionName':'us-east-1'}]}
            if method=='describe_availability_zones':return {'AvailabilityZones':[{'ZoneName':'us-east-1a'}]}
            if method=='describe_instance_types':return {'InstanceTypes':[{'InstanceType':'t3.small','VCpuInfo':{'DefaultVCpus':2},'MemoryInfo':{'SizeInMiB':2048}}]}
            if method=='describe_instance_type_offerings':return {'InstanceTypeOfferings':[{'InstanceType':'t3.small','Location':'us-east-1a'}]}
            if method=='describe_images':return {'Images':[{'ImageId':'ami-fixture','Name':'Ubuntu fixture','CreationDate':'2026-01-01','RootDeviceName':'/dev/sda1'}]}
            mappings={'vpc':('Vpc','VpcId','Vpcs'),'subnet':('Subnet','SubnetId','Subnets'),'internet_gateway':('InternetGateway','InternetGatewayId','InternetGateways'),'route_table':('RouteTable','RouteTableId','RouteTables'),'security_group':(None,'GroupId','SecurityGroups'),'volume':(None,'VolumeId','Volumes'),'instance':(None,'InstanceId','Reservations')}
            if method.startswith('create_') and method not in {'create_route'}:
                kind=method[7:];key,idkey,_=mappings[kind];identifier=kind+'-'+str(len(self.rows)+1)
                row={idkey:identifier,'Tags':params.get('TagSpecifications',[{'Tags':[]}])[0]['Tags'],'kind':kind}
                row.update({k:v for k,v in params.items() if k in {'VpcId','CidrBlock','AvailabilityZone'}})
                if kind=='vpc':row['State']='available'
                if kind=='internet_gateway':row['Attachments']=[]
                if kind=='route_table':row.update(Routes=[],Associations=[])
                if kind=='security_group':row['IpPermissions']=[]
                self.rows[identifier]=row
                return {key:copy.deepcopy(row)} if key else {idkey:identifier}
            if method=='run_instances':
                identifier='i-'+params['ClientToken'][:12]
                tags=params['TagSpecifications'][0]['Tags']
                self.rows[identifier]={'kind':'instance','InstanceId':identifier,'Tags':tags,'State':{'Name':'running'},'PublicIpAddress':'203.0.113.8','PrivateIpAddress':'10.42.1.8'}
                for index,_ in enumerate(params['BlockDeviceMappings']):
                    vid='vol-'+params['ClientToken'][:12]+'-'+str(index);self.rows[vid]={'kind':'volume','VolumeId':vid,'Tags':tags,'State':'in-use','instance':identifier}
                return {'Instances':[copy.deepcopy(self.rows[identifier])]}
            if method.startswith('describe_'):
                key=next((kind for kind,(_,_,collection) in mappings.items() if method=='describe_'+{'vpc':'vpcs','subnet':'subnets','internet_gateway':'internet_gateways','route_table':'route_tables','security_group':'security_groups','volume':'volumes','instance':'instances'}[kind]),None)
                _,idkey,collection=mappings[key];rows=[copy.deepcopy(row) for row in self.rows.values() if row['kind']==key]
                for name,value in params.items():
                    if name.endswith('Ids'):rows=[row for row in rows if row[idkey] in value]
                for filter in params.get('Filters',[]):
                    if filter['Name'].startswith('tag:'):rows=[row for row in rows if {t['Key']:t['Value'] for t in row.get('Tags',[])}.get(filter['Name'][4:]) in filter['Values']]
                if key=='instance':return {collection:[{'Instances':rows}] if rows else []}
                return {collection:rows}
            if method=='attach_internet_gateway':self.rows[params['InternetGatewayId']]['Attachments']=[{'VpcId':params['VpcId']}];return {}
            if method=='detach_internet_gateway':self.rows[params['InternetGatewayId']]['Attachments']=[];return {}
            if method=='create_route':self.rows[params['RouteTableId']]['Routes'].append(params);return {}
            if method=='associate_route_table':self.rows[params['RouteTableId']]['Associations'].append({'SubnetId':params['SubnetId'],'RouteTableAssociationId':'assoc-fixture'});return {}
            if method=='disassociate_route_table':return {}
            if method=='authorize_security_group_ingress':self.rows[params['GroupId']]['IpPermissions']=params['IpPermissions'];return {}
            if method=='terminate_instances':
                for identifier in params['InstanceIds']:
                    self.rows.pop(identifier,None)
                    for key,row in list(self.rows.items()):
                        if row.get('instance')==identifier:self.rows.pop(key)
                return {}
            if method.startswith('delete_'):
                identifier=next(iter(params.values()));self.rows.pop(identifier,None);return {}
            raise AssertionError(method)
        return invoke
    def borrowed(self):
        self.rows['vpc-borrowed']={'kind':'vpc','VpcId':'vpc-borrowed','CidrBlock':'10.3.0.0/16','Tags':[]}
        self.rows['subnet-borrowed']={'kind':'subnet','SubnetId':'subnet-borrowed','VpcId':'vpc-borrowed','CidrBlock':'10.3.1.0/24','AvailabilityZone':'us-east-1a','Tags':[]}
        return 'vpc-borrowed','subnet-borrowed'

class RestCloud:
    def __init__(self,kind):self.kind=kind;self.rows={};self.calls=[];self.fail=None;self.token='fixture-access-token'
    def factory(self,kind,credentials):
        adapter=(AzureProvider if kind=='azure' else GcpProvider)(credentials,client=httpx.AsyncClient(transport=httpx.MockTransport(self.respond)))
        adapter.own_client=True;adapter.token=self.token;adapter.expires=10**12
        return adapter
    def respond(self,request):
        path=unquote(urlsplit(str(request.url)).path);body=json.loads(request.content) if request.content else None
        self.calls.append((request.method,path,body))
        assert request.headers['authorization']=='Bearer '+self.token
        if self.fail and self.fail in path and request.method in {'PUT','POST','DELETE'}:return httpx.Response(403,json={'error':{'code':'AuthorizationFailed'}})
        azure=self.kind=='azure';root='/subscriptions/'+CREDENTIALS['azure']['subscription_id'] if azure else '/compute/v1/projects/fixture-project'
        if request.method=='GET':
            if path==root:return httpx.Response(200,json={'subscriptionId':CREDENTIALS['azure']['subscription_id']} if azure else {'name':'fixture-project'})
            if path.endswith('/locations'):return httpx.Response(200,json={'value':[{'name':'eastus','displayName':'East US'}]})
            if path.endswith('/skus'):return httpx.Response(200,json={'value':[{'name':'Standard_B2s','resourceType':'virtualMachines','locations':['eastus'],'capabilities':[{'name':'vCPUs','value':'2'},{'name':'MemoryGB','value':'4'}],'locationInfo':[{'location':'eastus','zones':['1']}]}]})
            if path.endswith('/regions'):return httpx.Response(200,json={'items':[{'name':'us-central1','zones':['https://www.googleapis.com/compute/v1/projects/fixture-project/zones/us-central1-a']}]})
            if path.endswith('/machineTypes'):return httpx.Response(200,json={'items':[{'name':'e2-small','guestCpus':2,'memoryMb':2048}]})
            if path.endswith('/operations/fixture'):return httpx.Response(200,json={'status':'DONE'})
            if path in self.rows:return httpx.Response(200,json=self.rows[path])
            if path.endswith(('/networks','/subnetworks','/disks','/virtualNetworks','/resources')):
                if path.endswith('/resources'):values=[value for key,value in self.rows.items() if key.startswith(path[:-10]+'/providers/')]
                elif azure and path.endswith('/virtualNetworks'):values=[value for key,value in self.rows.items() if '/virtualNetworks/' in key]
                else:values=[value for key,value in self.rows.items() if key.rsplit('/',1)[0]==path]
                return httpx.Response(200,json={'value' if azure else 'items':values})
            return httpx.Response(404,json={})
        if request.method in {'PUT','POST'}:
            if azure:
                row={**body,'id':path,'name':path.rsplit('/',1)[1]};row.setdefault('properties',{})['provisioningState']='Succeeded'
                if '/publicIPAddresses/' in path:row['properties']['ipAddress']='203.0.113.8'
                if '/networkInterfaces/' in path:row['properties']['ipConfigurations'][0]['properties']['privateIPAddress']='10.42.1.8'
                if '/virtualNetworks/' in path:
                    for subnet in row['properties'].get('subnets',[]):subnet['id']=path+'/subnets/'+subnet['name']
                if '/virtualMachines/' in path:
                    storage=row['properties']['storageProfile']
                    for disk in [storage['osDisk'],*storage['dataDisks']]:
                        diskpath=path.rsplit('/virtualMachines/',1)[0]+'/disks/'+disk['name']
                        self.rows[diskpath]={'id':diskpath,'name':disk['name'],'managedBy':path,'vm':path}
                self.rows[path]=row;return httpx.Response(201,json=row)
            row={**body,'selfLink':'https://www.googleapis.com'+path};target=path+'/'+body['name']
            row['selfLink']+='/'+body['name']
            if '/instances' in path:
                row['status']='RUNNING';row['networkInterfaces'][0].update(networkIP='10.42.1.8',accessConfigs=[{'natIP':'203.0.113.8'}])
                for disk in row['disks']:
                    value=disk['initializeParams'];disk_path=path.replace('/instances','/disks')+'/'+value['diskName']
                    self.rows[disk_path]={'name':value['diskName'],'selfLink':'https://www.googleapis.com'+disk_path,'labels':value['labels'],'vm':target}
            self.rows[target]=row;return httpx.Response(200,json={'selfLink':'https://www.googleapis.com'+root+'/global/operations/fixture'})
        if request.method=='DELETE':
            self.rows.pop(path,None)
            for key,value in list(self.rows.items()):
                if value.get('vm')==path:self.rows.pop(key)
            return httpx.Response(204) if azure else httpx.Response(200,json={'selfLink':'https://www.googleapis.com'+root+'/global/operations/fixture'})
        raise AssertionError((request.method,path))
    def borrowed(self):
        if self.kind=='azure':
            root='/subscriptions/'+CREDENTIALS['azure']['subscription_id']+'/resourceGroups/borrowed/providers/Microsoft.Network/virtualNetworks/existing';subnet=root+'/subnets/default'
            self.rows[root]={'id':root,'name':'existing','location':'eastus','properties':{'subnets':[{'id':subnet,'name':'default'}]}}
            return root,subnet
        root='/compute/v1/projects/fixture-project';network='https://www.googleapis.com'+root+'/global/networks/existing';subnet='https://www.googleapis.com'+root+'/regions/us-central1/subnetworks/existing'
        self.rows[root+'/global/networks/existing']={'name':'existing','selfLink':network}
        self.rows[root+'/regions/us-central1/subnetworks/existing']={'name':'existing','selfLink':subnet,'network':network,'ipCidrRange':'10.3.1.0/24'}
        return network,subnet

def cloud(kind):return AwsCloud() if kind=='aws' else RestCloud(kind)

def launch_plan(kind,account='account',**changes):
    from termx.runners.cloud.models import Launch
    values={'aws':('us-east-1','us-east-1a','t3.small','ami-fixture','gp3'),'azure':('eastus','1','Standard_B2s','Canonical:ubuntu-24_04-lts:server:latest','StandardSSD_LRS'),'gcp':('us-central1','us-central1-a','e2-small','projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64','pd-balanced')}
    region,zone,size,image,disk=values[kind]
    return Launch(request_id='fixture-request-123',account_id=account,project_id='project',name='Fixture machine',region=region,zone=zone,size=size,image=image,ssh_cidr='203.0.113.1/32',boot={'size_gb':32,'type':disk},volumes=[{'size_gb':64,'type':disk}],accept_cost=True,trust_machine=True,**changes).model_dump()

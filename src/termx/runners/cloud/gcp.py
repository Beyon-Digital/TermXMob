from __future__ import annotations
import uuid
from .base import RestProvider, CloudError, option, until

class GcpProvider(RestProvider):
    kind='gcp';base='https://compute.googleapis.com/compute/v1'
    async def api(self,method,path,*args,**kwargs):
        legacy='https://www.googleapis.com/compute/v1'
        if path.startswith(legacy+'/'):path=self.base+path[len(legacy):]
        return await super().api(method,path,*args,**kwargs)
    @property
    def project(self):return '/projects/'+self.credentials['project_id']
    async def identity(self):
        row=await self.api('GET',self.project)
        return {'account':row['name'],'principal':self.credentials['client_email']}
    async def items(self,path):
        rows=[]
        while path:
            value=await self.api('GET',path);rows+=value.get('items',[])
            from urllib.parse import quote
            path=path.split('?')[0]+'?pageToken='+quote(value['nextPageToken']) if value.get('nextPageToken') else None
        return rows
    async def catalog(self,region='',zone=''):
        regions=await self.items(self.project+'/regions');result={'regions':[option(r['name']) for r in regions]}
        if not region:return result
        selected=next((r for r in regions if r['name']==region),None)
        if not selected:raise CloudError('Region is not available in this project.',definitive=True)
        zones=[z.rsplit('/',1)[1] for z in selected['zones']];zone=zone or zones[0]
        if zone not in zones:raise CloudError('Zone does not belong to the selected region.',definitive=True)
        types=await self.items(self.project+'/zones/'+zone+'/machineTypes')
        networks=await self.items(self.project+'/global/networks');subnets=await self.items(self.project+'/regions/'+region+'/subnetworks')
        result.update(zones=[option(z) for z in zones],sizes=[option(t['name'],f"{t['name']} · {t['guestCpus']} vCPU · {t['memoryMb']//1024} GiB") for t in types if not t['name'].startswith(('t2a-','c4a-'))],images=[option('projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64','Ubuntu 24.04 LTS · x86_64')],networks=[option(n['selfLink'],n['name']) for n in networks],subnets=[option(s['selfLink'],s['name']+' · '+s['ipCidrRange'],network=s['network']) for s in subnets],volume_types=[option('pd-balanced','Balanced persistent disk'),option('pd-ssd','SSD persistent disk'),option('pd-standard','Standard persistent disk')])
        return result
    def path(self,kind,name,d):
        p=d['plan'];scope='/zones/'+p['zone'] if kind in {'vm','disk'} else '/regions/'+p['region'] if kind=='subnet' else '/global'
        collection={'vm':'instances','disk':'disks','network':'networks','subnet':'subnetworks','firewall':'firewalls'}[kind]
        return self.project+scope+'/'+collection+('/'+name if name else '')
    def owned(self,row,d):
        if row.get('labels',{}).get('termx-deployment')!=d['id'] and row.get('description')!='termx-deployment:'+d['id']:
            raise CloudError('GCP resource is not owned by this deployment.',definitive=True)
    async def find(self,kind,name,d):
        row=await self.api('GET',self.path(kind,name,d),missing=True)
        if row is None:return None
        self.owned(row,d);return {'id':row['selfLink']}
    async def operation(self,value):
        if not value:return
        async def read():
            row=await self.api('GET',value['selfLink'])
            if row.get('error'):raise CloudError('GCP operation failed. Check quotas, permissions and resource configuration.')
            return row if row['status']=='DONE' else None
        await until(read)
    async def create(self,kind,name,params,d):
        body={**params,'name':name,'description':'termx-deployment:'+d['id']}
        if kind in {'vm','disk'}:body['labels']={'termx-deployment':d['id']}
        token=str(uuid.uuid5(uuid.UUID(d['id']),kind+':'+name))
        value=await self.api('POST',self.path(kind,'',d)+'?requestId='+token,body)
        await self.operation(value)
        return await self.find(kind,name,d)
    async def provision(self,d,ensure,userdata):
        p=d['plan'];name='termx-'+d['id'][:20]
        if p['network_mode']=='managed':
            network=(await ensure('network',name,{'autoCreateSubnetworks':False}))['id']
            subnet=(await ensure('subnet',name,{'network':network,'ipCidrRange':p['subnet_cidr'],'privateIpGoogleAccess':True}))['id']
        else:network=p['network'];subnet=p['subnet']
        await ensure('firewall',name,{'network':network,'direction':'INGRESS','allowed':[{'IPProtocol':'tcp','ports':['22']}],'sourceRanges':[p['ssh_cidr']],'targetTags':[name]})
        disks=[]
        for index,volume in enumerate([p['boot'],*p['volumes']]):
            params={'diskSizeGb':str(volume['size_gb']),'diskType':self.project+'/zones/'+p['zone']+'/diskTypes/'+volume['type'],'diskName':name+'-disk-'+str(index),'labels':{'termx-deployment':d['id']}}
            if index==0:params['sourceImage']=p['image']
            disks.append({'boot':index==0,'autoDelete':True,'type':'PERSISTENT','initializeParams':params})
        interface={'network':network,'subnetwork':subnet}
        if p['public_ip']:interface['accessConfigs']=[{'name':'External NAT','type':'ONE_TO_ONE_NAT'}]
        return await ensure('vm',name,{'machineType':self.project+'/zones/'+p['zone']+'/machineTypes/'+p['size'],'disks':disks,'networkInterfaces':[interface],'serviceAccounts':[],'tags':{'items':[name]},'metadata':{'items':[{'key':'user-data','value':userdata},{'key':'enable-oslogin','value':'FALSE'},{'key':'block-project-ssh-keys','value':'TRUE'}]},'shieldedInstanceConfig':{'enableVtpm':True,'enableIntegrityMonitoring':True}})
    async def machine(self,d):
        vm=next(r for r in d['resources'] if r['kind']=='vm' and r.get('id'))
        row=await self.api('GET',vm['id'])
        if row['status']!='RUNNING':return None
        interface=row['networkInterfaces'][0]
        host=(interface.get('accessConfigs') or [{}])[0].get('natIP') if d['plan']['public_ip'] else interface.get('networkIP')
        return {'host':host,'instance_id':row['name']}
    async def delete(self,resource,d):
        found=await self.find(resource['kind'],resource['name'],d)
        if not found:return
        if found['id']!=resource['id']:raise CloudError('GCP resource identity changed; refusing cleanup.')
        await self.operation(await self.api('DELETE',resource['id'],missing=True))
        await until(lambda:self.find(resource['kind'],resource['name'],d),lambda value:value is None)
    async def leftovers(self,d):
        rows=await self.items(self.path('disk','',d))
        return [{'kind':'disk','name':row['name'],'id':row['selfLink']} for row in rows if row.get('labels',{}).get('termx-deployment')==d['id']]

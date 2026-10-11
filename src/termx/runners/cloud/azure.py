from __future__ import annotations
import base64
from .base import RestProvider, CloudError, option, until, require_owned

class AzureProvider(RestProvider):
    kind='azure';base='https://management.azure.com'
    @property
    def subscription(self):return '/subscriptions/'+self.credentials['subscription_id']
    def version(self,kind):return '2024-07-01' if kind=='vm' else '2024-05-01' if kind in {'network','subnet','nsg','ip','nic'} else '2021-04-01'
    def path(self,kind,name,d):
        group=self.subscription+'/resourceGroups/termx-'+d['id'][:20]
        if kind=='group':return group+'?api-version=2021-04-01'
        collection={'vm':'Microsoft.Compute/virtualMachines','network':'Microsoft.Network/virtualNetworks','nsg':'Microsoft.Network/networkSecurityGroups','ip':'Microsoft.Network/publicIPAddresses','nic':'Microsoft.Network/networkInterfaces'}[kind]
        return group+'/providers/'+collection+'/'+name+'?api-version='+self.version(kind)
    async def identity(self):
        value=await self.api('GET',self.subscription+'?api-version=2021-04-01')
        return {'account':value['subscriptionId'],'principal':self.credentials['client_id'],'tenant':self.credentials['tenant_id']}
    async def items(self,path):
        rows=[]
        while path:
            value=await self.api('GET',path);rows+=value.get('value',[]);path=value.get('nextLink')
        return rows
    async def catalog(self,region='',zone=''):
        locations=await self.items(self.subscription+'/locations?api-version=2022-12-01')
        result={'regions':[option(r['name'],r.get('displayName')) for r in locations if r.get('metadata',{}).get('regionType','Physical')=='Physical']}
        if not region:return result
        skus=await self.items(self.subscription+"/providers/Microsoft.Compute/skus?api-version=2021-07-01&$filter=location%20eq%20'"+region+"'")
        sizes=[];zones=set()
        for sku in skus:
            if sku['resourceType']!='virtualMachines' or region not in sku.get('locations',[]):continue
            caps={c['name']:c['value'] for c in sku.get('capabilities',[])}
            if caps.get('CpuArchitectureType','x64')!='x64':continue
            restrictions=sku.get('restrictions',[])
            if any(r['type']=='Location' for r in restrictions):continue
            available={z for info in sku.get('locationInfo',[]) if info['location']==region for z in info.get('zones',[])}
            for restriction in restrictions:available-=set(restriction.get('restrictionInfo',{}).get('zones',[]))
            zones|=available
            if zone and zone not in available:continue
            sizes.append(option(sku['name'],f"{sku['name']} · {caps.get('vCPUs','?')} vCPU · {caps.get('MemoryGB','?')} GiB"))
        networks=await self.items(self.subscription+'/providers/Microsoft.Network/virtualNetworks?api-version=2024-05-01')
        networks=[n for n in networks if n['location'].lower()==region.lower()]
        result.update(zones=[option('','Regional placement'),*[option(z,'Zone '+z) for z in sorted(zones)]],sizes=sizes,images=[option('Canonical:ubuntu-24_04-lts:server:latest','Ubuntu 24.04 LTS · x86_64')],networks=[option(n['id'],n['name']) for n in networks],subnets=[option(s['id'],s['name'],network=n['id']) for n in networks for s in n['properties'].get('subnets',[])],volume_types=[option('StandardSSD_LRS','Standard SSD'),option('Premium_LRS','Premium SSD'),option('Standard_LRS','Standard HDD')])
        return result
    async def find(self,kind,name,d):
        row=await self.api('GET',self.path(kind,name,d),missing=True)
        if row is None:return None
        require_owned(row,d['id'],'azure');return {'id':row['id']}
    async def create(self,kind,name,params,d):
        path=self.path(kind,name,d)
        await self.api('PUT',path,{**params,'location':d['plan']['region'],'tags':{'termx-deployment':d['id']}})
        async def ready():
            row=await self.api('GET',path,missing=True)
            if row is None:return None
            status=row.get('properties',{}).get('provisioningState','Succeeded')
            if status in {'Failed','Canceled'}:raise CloudError('Azure resource provisioning failed. Inspect deployment quota and permissions.')
            return {'id':row['id']} if status=='Succeeded' else None
        return await until(ready)
    async def provision(self,d,ensure,userdata):
        p=d['plan'];name='termx-'+d['id'][:20]
        await ensure('group',name,{})
        if p['network_mode']=='managed':
            network=await ensure('network',name,{'properties':{'addressSpace':{'addressPrefixes':[p['network_cidr']]},'subnets':[{'name':'runner','properties':{'addressPrefix':p['subnet_cidr']}}]}})
            subnet=network['id']+'/subnets/runner'
        else:subnet=p['subnet']
        nsg=await ensure('nsg',name,{'properties':{'securityRules':[{'name':'TermX-SSH','properties':{'priority':1000,'direction':'Inbound','access':'Allow','protocol':'Tcp','sourcePortRange':'*','destinationPortRange':'22','sourceAddressPrefix':p['ssh_cidr'],'destinationAddressPrefix':'*'}}]}})
        ip=await ensure('ip',name,{'sku':{'name':'Standard'},'properties':{'publicIPAllocationMethod':'Static'},**({'zones':[p['zone']]} if p['zone'] else {})}) if p['public_ip'] else None
        ipconfig={'subnet':{'id':subnet},'privateIPAllocationMethod':'Dynamic'}
        if ip:ipconfig['publicIPAddress']={'id':ip['id']}
        nic=await ensure('nic',name,{'properties':{'networkSecurityGroup':{'id':nsg['id']},'ipConfigurations':[{'name':'primary','properties':ipconfig}]}})
        publisher,offer,sku,version=p['image'].split(':')
        # VM deletion deletes only disks created inline by this request. Existing
        # networks/subnets are references, never resources in this deployment.
        storage={'imageReference':{'publisher':publisher,'offer':offer,'sku':sku,'version':version},'osDisk':{'name':name+'-os','createOption':'FromImage','deleteOption':'Delete','diskSizeGB':p['boot']['size_gb'],'managedDisk':{'storageAccountType':p['boot']['type']}},'dataDisks':[{'lun':i,'name':name+'-data-'+str(i),'createOption':'Empty','deleteOption':'Delete','diskSizeGB':v['size_gb'],'managedDisk':{'storageAccountType':v['type']}} for i,v in enumerate(p['volumes'])]}
        # Azure requires a Linux SSH public key in osProfile even when cloud-init supplies it.
        import yaml
        public=yaml.safe_load(userdata)['users'][0]['ssh_authorized_keys'][0]
        return await ensure('vm',name,{'properties':{'hardwareProfile':{'vmSize':p['size']},'storageProfile':storage,'osProfile':{'computerName':name,'adminUsername':'termx','customData':base64.b64encode(userdata.encode()).decode(),'linuxConfiguration':{'disablePasswordAuthentication':True,'ssh':{'publicKeys':[{'path':'/home/termx/.ssh/authorized_keys','keyData':public}]}}},'networkProfile':{'networkInterfaces':[{'id':nic['id'],'properties':{'primary':True}}]}},**({'zones':[p['zone']]} if p['zone'] else {})})
    async def machine(self,d):
        kind='ip' if d['plan']['public_ip'] else 'nic';resource=next(r for r in d['resources'] if r['kind']==kind and r.get('id'))
        row=await self.api('GET',self.path(kind,resource['name'],d))
        host=row['properties'].get('ipAddress') if kind=='ip' else row['properties']['ipConfigurations'][0]['properties'].get('privateIPAddress')
        return {'host':host,'instance_id':'termx-'+d['id'][:20]} if host else None
    async def delete(self,resource,d):
        found=await self.find(resource['kind'],resource['name'],d)
        if not found:return
        if found['id']!=resource['id']:raise CloudError('Azure resource identity changed; refusing cleanup.')
        if resource['kind']=='group':
            async def empty():return not await self.items(resource['id']+'/resources?api-version=2021-04-01')
            # Wait for inline disks to disappear. Never delete a nonempty group,
            # including resources someone else may have subsequently added.
            await until(empty,attempts=60)
        await self.api('DELETE',self.path(resource['kind'],resource['name'],d),missing=True)
        await until(lambda:self.find(resource['kind'],resource['name'],d),lambda value:value is None)
    async def leftovers(self,d):return [] # Dedicated group cannot be deleted until all VM disks are gone.

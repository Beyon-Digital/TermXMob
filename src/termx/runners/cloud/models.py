from __future__ import annotations
import ipaddress
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Volume(BaseModel):
    model_config=ConfigDict(extra='forbid')
    size_gb:int=Field(default=32,ge=8,le=16384)
    type:str=Field(min_length=1,max_length=80)

class Launch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{8,64}$')
    account_id:str
    project_id:str
    name:str=Field(min_length=1,max_length=80)
    region:str=Field(pattern=r'^[a-z0-9-]{1,40}$')
    zone:str=Field(default='',pattern=r'^[a-zA-Z0-9-]{0,80}$')
    size:str=Field(min_length=1,max_length=100)
    image:str=Field(min_length=1,max_length=300)
    network_mode:Literal['managed','existing']='managed'
    network:str=Field(default='',max_length=500)
    subnet:str=Field(default='',max_length=500)
    network_cidr:str='10.42.0.0/16'
    subnet_cidr:str='10.42.1.0/24'
    ssh_cidr:str
    public_ip:bool=True
    allow_sudo:bool=False
    boot:Volume
    volumes:list[Volume]=Field(default_factory=list,max_length=4)
    cleanup:Literal['manual','lifetime','after-task']='lifetime'
    lifetime_hours:float=Field(default=1,ge=.25,le=168)
    accept_cost:bool=False
    trust_machine:bool=False

    @model_validator(mode='after')
    def valid(self):
        if not self.accept_cost or not self.trust_machine:raise ValueError('Accept cloud billing and machine execution permissions')
        for field in ('network_cidr','subnet_cidr','ssh_cidr'):
            network=ipaddress.ip_network(getattr(self,field),strict=True)
            if network.version!=4:raise ValueError('Choose IPv4 network ranges')
        if not ipaddress.ip_network(self.subnet_cidr).subnet_of(ipaddress.ip_network(self.network_cidr)):
            raise ValueError('The subnet must be inside the network range')
        if self.network_mode=='existing' and (not self.network or not self.subnet):raise ValueError('Select an existing network and subnet')
        if self.network_mode=='managed' and not self.public_ip:raise ValueError('Private-only machines require an existing subnet with outbound internet access and a route from the control plane')
        return self

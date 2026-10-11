"""New machine SSH identity is pinned before boot, without TOFU or uploaded cloud keys."""
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives import serialization
import yaml

def key_pair(user=False):
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072) if user else ed25519.Ed25519PrivateKey.generate()
    return (key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()).decode(),key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode())

def identities():
    private,public=key_pair(True);host_private,host_public=key_pair()
    return {'private':private,'public':public,'host_private':host_private,'host_public':host_public}

def cloud_init(keys,allow_sudo=False):
    script='''#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv ca-certificates openssh-server sudo
__TERMX_PERMISSIONS__
python3 -m venv /opt/termx-bootstrap
/opt/termx-bootstrap/bin/pip install 'uv==0.11.14'
export UV_PYTHON_INSTALL_DIR=/opt/termx-python
/opt/termx-bootstrap/bin/uv python install 3.14
PYTHON=$(/opt/termx-bootstrap/bin/uv python find --managed-python 3.14)
ln -sfn "$PYTHON" /usr/local/bin/termx-python
mkdir -p /home/termx/workspace
chown termx:termx /home/termx/workspace
'''
    # Azure's guest agent may grant its osProfile administrator sudo before
    # cloud-init runs. Explicitly remove that grant for standard-user runners.
    permissions='''for group in sudo admin wheel google-sudoers; do
  if getent group "$group" >/dev/null; then gpasswd -d termx "$group" >/dev/null 2>&1 || true; fi
done
for file in /etc/sudoers.d/*; do
  if [ -f "$file" ]; then sed -i '/^[[:space:]]*termx[[:space:]]/d' "$file"; fi
done
if runuser -u termx -- sudo -n true >/dev/null 2>&1; then
  echo 'Unexpected administrator access for standard-user runner' >&2
  exit 1
fi''' if not allow_sudo else ':'
    script=script.replace('__TERMX_PERMISSIONS__',permissions)
    return '#cloud-config\n'+yaml.safe_dump({'users':[{'name':'termx','shell':'/bin/bash','lock_passwd':True,'sudo':'ALL=(ALL) NOPASSWD:ALL' if allow_sudo else False,'ssh_authorized_keys':[keys['public']]}],
        'ssh_pwauth':False,'disable_root':True,'ssh_keys':{'ed25519_private':keys['host_private'],'ed25519_public':keys['host_public']},
        'write_files':[{'path':'/opt/termx-bootstrap.sh','permissions':'0700','content':script}],
        'runcmd':[['bash','/opt/termx-bootstrap.sh']]},sort_keys=False)

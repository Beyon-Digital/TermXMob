"""A temporary loopback-only SSH server using throwaway keys and this test user."""
import os
import pwd
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def ssh_machine(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for name in ('host','client'):
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(root/name)],check=True)
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];listener.close()
    public=(root/'host.pub').read_text().split()
    (root/'known_hosts').write_text(f'[127.0.0.1]:{port} {public[0]} {public[1]}\n')
    user=pwd.getpwuid(os.getuid()).pw_name
    (root/'sshd_config').write_text(f'''ListenAddress 127.0.0.1
Port {port}
HostKey {root}/host
PidFile {root}/pid
AuthorizedKeysFile {root}/client.pub
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowUsers {user}
AllowTcpForwarding no
X11Forwarding no
AcceptEnv HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE PIP_INDEX_URL PIP_TRUSTED_HOST
''')
    log=(root/'sshd.log').open('w+')
    process=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(root/'sshd_config')],stderr=log)
    try:
        deadline=time.monotonic()+5
        while True:
            if process.poll() is not None:
                log.seek(0);raise RuntimeError(log.read())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.05)
        yield {'name':'Loopback SSH machine','provider':'ssh','host':'127.0.0.1','port':port,'user':user,
               'root':str(root/'workspace'),'identity_file':str(root/'client'),'known_hosts_file':str(root/'known_hosts'),
               'region':'','profile':'','instance_id':'','trust_machine':True,'policy_version':1}
    finally:
        process.terminate();process.wait(timeout=5);log.close()

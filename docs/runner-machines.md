# Cloud and persistent runner machines

## Provision a machine from the desktop

Open **Models & tools → Cloud accounts** in Workspace settings. Verify and save
an AWS, Azure, or GCP account, then open **Runners → Create cloud machine**.
The control plane provisions infrastructure directly; no cloud CLI, uploaded SSH
key, manually installed worker, or mobile setup is required for this workflow.

| Provider | Authentication | Required account capability |
| --- | --- | --- |
| AWS EC2 | IAM access key and secret; session token for temporary credentials | STS identity, EC2 catalog/describe, tagged instance and volume creation/deletion, and VPC/subnet/route/Internet gateway/security group operations |
| Azure | Tenant, subscription, application/client ID and service principal secret | Contributor on the subscription; Microsoft.Compute and Microsoft.Network registered |
| GCP Compute Engine | Service-account JSON key | Compute Engine API enabled; Compute Admin on the selected project |

Authentication must have provisioning permissions, sufficient quota and billing
enabled. Valid read-only credentials can authenticate but cannot create machines.
Credentials stay in the host OS keychain/Secret Service, never SQLite, browser
storage or runner configuration. The host must have a working secure credential
store. Update expired credentials using **Update authentication**; replacements
must belong to the same account/project/subscription and Azure tenant. Accounts
cannot be removed until their deployments have been cleaned up.

Choose a project, region, availability zone, x86 Linux machine size and Ubuntu
24.04 image from the provider catalog, a boot disk and up to four data volumes.
Choose a dedicated network or an existing VPC/VNet and subnet. Set the allowed
SSH source CIDR to the **control plane host's** egress IP/range, not the phone or
browser's address. Optional private-only access requires an existing subnet with
outbound internet access and a route from the control plane. TermX does not create
VPNs, peering or NAT gateways. New managed networks require a public IP.

Review the configuration and authorize provider charges before choosing
**Create reviewed machine**. This review is valid for five minutes and is bound
to the project authority, account revision and exact configuration. Retrying a
lost launch response with the same request ID returns the original deployment.

TermX generates SSH client and host keys before boot and pins the host key. Its
cloud-init script installs Python 3.14 and the worker dependencies. Installation
needs access to Ubuntu repositories, PyPI and Python downloads from GitHub.
The agent uses `/home/termx/workspace` as the `termx` user; passwordless sudo is an
explicit choice. No provider account credentials or cloud instance role/service
account are assigned to the VM. Boot disks contain the runtime; additional disks
are attached blank and require formatting/mounting before use. The agent's
filesystem permissions and any administrator access determine whether it may
perform that work.

When the deployment is **ready**, select its name in the session's **Execution**
settings. Cloud machines use the same SSH worker and host-side model credential
broker as registered machines. Their control, task review and history remain
available through existing desktop/browser and mobile companion flows.

### Cleanup and recovery

Choose manual cleanup, a maximum lifetime, or cleanup after the first task ends
(with a maximum lifetime). Automatic cleanup requires the control plane to stay
online; deadlines that pass offline are processed after restart. An interrupted
provisioning operation is reconciled and cleaned up after restart, never replayed.

**Clean up machine → Delete managed resources** revokes new work, stops the
active task and deletes the VM, boot/data disks, managed firewall, public IP and
dedicated networking. It permanently deletes machine files. Export needed results
first. Borrowed networks/subnets are preserved. Resource identities and ownership
tags are checked before deletion. Azure cleanup refuses to delete a nonempty
resource group, including unrelated resources added to it by another actor.

Each create/delete intent and resource identity is journaled before the next
operation. Lost responses are reconciled against provider state. If the outcome
cannot be confirmed, the card shows **cleanup-failed** and retains credentials
and resource details for **Retry cleanup**. It does not claim deletion or silently
create replacement machines. Restore provider access or resolve the displayed
resource conflict in the provider console, then retry. Provider charges may
continue until cleanup is confirmed. Prices, quotas and capacity remain subject
to the cloud account; TermX does not provide price estimates or reserve capacity.

On-demand creation currently supports EC2, Azure VMs and GCP Compute Engine in
their public commercial clouds. Lightsail and other providers remain supported
through existing-machine SSH enrollment below. Windows/ARM images, interactive
cloud OAuth, managed identities, attaching existing disks and automatic replacement
of a deleted scheduled-task runner are not part of provisioning. Select a live
runner before dispatching a schedule; a deleted runner is never recreated implicitly.

## Existing machine enrollment

Runners in the desktop control plane support two execution modes:

| Mode | Execution permissions | Storage and lifetime |
| --- | --- | --- |
| Machine | Commands run as the configured SSH user, with that account's filesystem, network, installed tools, and instance credentials | Workspace and machine persist between tasks |
| Isolated container | Existing nonroot Docker boundary, resource limits and isolated networking for agents | Scratch workspace and expiring container lease |

Use **Runners → Add machine** to register an existing Linux instance. EC2,
Lightsail, Azure, GCP, other VPS providers, on-premises machines and reachable
developer machines share the SSH transport. Registration stores connection
configuration; it does not purchase, launch, terminate or delete cloud resources.

## Connect and set up

1. Create a dedicated SSH account on the machine with the permissions agents
   should have. Install Python 3.14 or later with `venv`, and allow access to its
   configured Python package index. TermX does not install OS packages or sudo.
2. On the **control plane host**, configure SSH authentication and verify the
   machine's host key against a trusted source. An SSH config alias, an explicit
   private key path, or the host's SSH agent can supply authentication. SSH agent
   forwarding and port forwarding are disabled. Unknown or changed host keys
   cause a connection failure; TermX never accepts them automatically.
3. Choose a project, machine name, hostname, SSH username and port, and an
   absolute persistent workspace directory such as `/home/ubuntu/workspace`.
   Optional key and known-hosts paths refer to files on the control plane.
   The setup Python executable refers to the **remote** machine.
4. Accept execution with the SSH account's permissions and register the machine.
5. Choose **Set up runtime** and review the action. TermX transfers this host's
   worker source, creates `<workspace>/.termx-runtime`, and installs the Python
   dependencies in that private environment. Setup runs in the background;
   its durable status is visible on the card. **Check connection** probes an
   already installed runtime. A machine becomes selectable only when ready.
6. In a session's **Execution** settings, select the machine, a configured API
   account, and an explicit model. The internal agent uses the configured remote
   root. File operations, shell commands, plans and approval decisions are
   recorded in the existing canonical task history. Provider keys remain in the
   control plane's credential broker; they are not sent to the machine.

Files and installed tools on the machine remain available for subsequent tasks.
Use SSH/SFTP or reviewed agent operations to populate the workspace and retrieve
results. The container-only tar upload, artifact download and raw command job
endpoints do not accept machine targets. TermX runtime and control directories
are excluded from ordinary workspace indexing.

Machine connection settings are immutable after enrollment. Remove and register
a new connection to change its destination or project; existing session and
schedule bindings cannot silently move to a different machine.

## Control and scheduling

**Disable execution** revokes new work and requests cancellation of an active
TermX agent. It does not power off the instance. **Check connection** explicitly
reenables an available machine. **Remove connection** disables the target while
retaining its task history, files and cloud resource. Reenrollment creates a new
target ID. Enrollments invalidated by a host policy change also require a new ID.

Agents support the existing host-reviewed tool set and API accounts. Native
Codex/Claude engines, desktop/computer streaming and browser handoff are not
provided by the SSH worker. Shell commands inherit the SSH user's permissions;
the workspace root is a file-tool scope, not an OS sandbox. Resource controls
come from the VM/account configuration. Use containers when CPU/memory limits
or kernel isolation are required.

Scheduled **agent tasks** use their session's selected machine and existing
delegation, budget, overlap and missed-run policies. The control plane must stay
online to dispatch and broker them. Model-free shell cron jobs still execute on
the control plane host. Existing mobile task observation and approval controls
continue through the host; infrastructure setup belongs to the desktop UI.

Each agent run has a remote watchdog with a maximum one-hour budget, including
provider planning and approval waits. Disconnection, revocation and restart do
not replay work. A failed cancellation is shown as `stop-unconfirmed`; verify
the machine before reconnecting. Normal cancellation cleans up tracked command
processes; intentionally detached services are machine workloads and may outlive
the agent, as permitted by the SSH account.

## EC2 and Lightsail lifecycle

Choose the EC2 or Lightsail provider to record an instance ID/name, region, and
optional AWS profile. Install AWS CLI v2 on the control plane and configure its
credential chain or named profile there. TermX stores profile references only.

The card provides **Cloud status**, **Start instance**, **Stop instance** and
**Reboot instance**. Mutations require review of the concrete instance and are
refused while a TermX task is active. Power operations affect every workload on
the instance. After a power transition, check its SSH connection before running
tasks. An API timeout produces an unknown outcome, not automatic replay.

EC2 uses `DescribeInstances`, `StartInstances`, `StopInstances`, and
`RebootInstances`; Lightsail uses `GetInstanceState`, `StartInstance`,
`StopInstance`, and `RebootInstance`. Scope the control plane's IAM access to
the intended instances. Other platforms use the generic SSH mode with lifecycle
managed through their provider console. Use a stable hostname or static IP if
an instance's public address changes when it restarts.

## API and validation

- `GET/POST /api/cloud/accounts`, `PUT/DELETE /api/cloud/accounts/{id}`:
  verify, store, rotate and remove provider authentication.
- `GET /api/cloud/accounts/{id}/catalog?region=...&zone=...`: provider choices.
- `POST /api/cloud/preview`: validate and review a concrete configuration.
- `POST /api/cloud/deployments`: launch the reviewed configuration (202).
- `GET /api/cloud/deployments`: durable provisioning and resource state.
- `POST /api/cloud/deployments/{id}/cleanup`: reconcile and delete resources (202).

- `POST /api/runners/machines`: register an owner/project-scoped machine.
- `POST /api/runners/machines/{id}/setup`: start runtime setup (202).
- `POST /api/runners/machines/{id}/connect`: check and enable execution.
- `POST /api/runners/machines/{id}/cloud`: status/start/stop/reboot.
- Existing `/api/runners`, `/stop`, DELETE, and `/jobs` list records and history.

All routes require managed host-admin access; registration, connection, setup and
cloud actions also require the project's agent-run permission. Existing session
and task authorization remains enforced throughout agent execution.

Run tests with a scratch `TERMX_CONFIG_DIR`. `TERMX_TEST_SSH=1` enables real
loopback SSH tests using temporary host/client keys and a loopback-only sshd.
`TERMX_TEST_SSH_SETUP=1` separately enables a fresh runtime installation test
with package downloads; only its loopback fixture forwards the test environment's
proxy and CA settings.

`tests/test_cloud_runners.py` runs production adapters against stateful mock cloud
APIs, validating AWS SDK request shapes and Azure/GCP HTTP operations. It covers
create/use/delete, borrowed networks, lost responses, cleanup retries, ownership,
automatic expiry, task completion and restart recovery. Agent execution uses a
real local worker. `tests/test_cloud_auth.py` exercises OAuth/JWT token refresh and
API destination checks without sending real credentials.

`tests/workspace_cloud_e2e.py <output-directory>` drives the rendered desktop
Settings, launch review, execution selector and cleanup for all three providers,
using mock infrastructure APIs and a real loopback SSH worker. It writes browser
screenshots and a JSON report. This is not a live provider qualification: cloud-init,
actual quotas, capacity, routing and billing need a separate run in an authorized
provider account. No billable machines are created by these tests.
`tests/workspace_machine_e2e.py <output-directory>` exercises the rendered desktop
and saves screenshots. AWS adapter tests use fixtures, so they create no cloud
resources and make no paid provider calls.

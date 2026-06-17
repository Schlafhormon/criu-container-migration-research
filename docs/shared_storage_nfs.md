# Shared Storage With NFS

The experiments use a shared path for logs, bundles, CRIU images, and batch
outputs. The original lab used NFSv4.2 with `/mnt/criu` mounted on all hosts.

## Layout

| Path | Purpose |
|---|---|
| `/mnt/criu/logs` | Manual monitor and migration logs. |
| `/mnt/criu/runc-bundle` | Shared runc bundle. |
| `/mnt/criu/runc/<name>/<cp_name>/` | CRIU checkpoint images. |
| `/mnt/criu/runs` | `clm` run and batch outputs. |

## Example Topology

| Role | Example |
|---|---|
| NFS server | Source host |
| NFS clients | Destination and monitor hosts |
| Export | `/share` |
| Mount point | `/mnt/criu` |
| Protocol | NFSv4.2 |

The example lab network was `192.168.13.0/24`; replace this with the local
experiment network.

## Server Setup

```bash
sudo apt-get update
sudo apt-get install -y nfs-kernel-server
sudo systemctl enable --now nfs-server
sudo mkdir -p /share /share/logs /share/runc /share/runc-bundle /share/runs
```

Example `/etc/exports` entry:

```text
/share 192.168.13.0/24(rw,sync,no_subtree_check,no_root_squash)
```

Apply and inspect:

```bash
sudo exportfs -ra
sudo exportfs -v
```

Bind the export to the same path used by all scripts:

```bash
sudo mkdir -p /mnt/criu
sudo mount --bind /share /mnt/criu
```

Optional `/etc/fstab` entry:

```text
/share /mnt/criu none bind 0 0
```

## Client Setup

```bash
sudo apt-get update
sudo apt-get install -y nfs-common
sudo mkdir -p /mnt/criu
```

Example `/etc/fstab` entry:

```text
192.168.13.10:/share /mnt/criu nfs4 vers=4.2,_netdev,defaults 0 0
```

Mount and verify:

```bash
sudo mount -a
mount | grep /mnt/criu
```

## Permissions

The monitor and migration scripts write some artifacts as the experiment user.
Writing to `/mnt/criu/logs` should therefore work without `sudo`.

Example shared group:

```bash
sudo groupadd -g 3137 -f criu
sudo usermod -aG criu <experiment-user>
```

On the server:

```bash
sudo chgrp -R criu /share
sudo chmod -R 2775 /share
sudo apt-get install -y acl
sudo setfacl -R -m g:criu:rwx /share
sudo setfacl -R -d -m g:criu:rwx /share
```

Write test on every host:

```bash
mkdir -p /mnt/criu/logs
touch /mnt/criu/logs/.write_test_$HOSTNAME
```

## Methodological Note

Shared storage can affect measured downtime. In pre-copy, whether checkpoint
images are copied after the final dump or restored directly from the shared path
changes the critical path. Record the image mode and storage path in every run.

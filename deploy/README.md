# Running the engine on a cloud VM

The engine runs on a small Google Cloud VM and is reached only over Tailscale. These are your steps for
the VM itself (steps 1-3 and 6-7 of the move). Copying your data over and connecting the apps come in
between and after; the design doc lists them all.

Steps marked **verify at set-up** come from Google's and Tailscale's pages as they read on 2026-11-26.
Screens change, so check them as you go.

## 1. Create the VM

In the Google Cloud console, go to Compute Engine, then **Create instance**:

- **Region:** `us-west1` (Oregon). It is one of the three regions with a free e2-micro.
- **Machine:** series E2, type `e2-micro`.
- **Boot disk:** Ubuntu, version **Ubuntu 24.04 LTS**, type **Standard persistent disk**, size **30 GB**.
  (The free tier covers 30 GB-months of standard disk.)
- **Firewall:** leave HTTP and HTTPS **unticked**.
- Leave the external IP on. The VM needs it to reach Gmail and Anthropic.

**Set a $1 budget alert** (do this now): Billing, then Budgets & alerts, then **Create budget**. Amount
$1, and keep the default email alerts. The free tier is not a promise that nothing is charged, for
example the external IP or more than 1 GB of outbound data a month may cost a little.

Connect with the console's **SSH** button for now (the browser window).

## 2. Tailscale, then close the public doors

On the VM:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```

The first command downloads and runs Tailscale's install script from tailscale.com. Check it against
Tailscale's current Linux install page (<https://tailscale.com/kb/1031/install-linux>) before you run it
(**verify at set-up**).

The second command prints a link. Open it and sign in to your tailnet. Then publish the API:

```bash
sudo tailscale serve --bg 47615
tailscale serve status
```

Note the VM's name. Its address is `https://<vm-name>.<your-tailnet>.ts.net`. **Verify at set-up:** HTTPS
must be enabled for your tailnet in the Tailscale admin console (DNS page) for `tailscale serve` to work.

**Prove Tailscale SSH works before you close anything.** From your PC (with Tailscale on), run
`ssh <your-user>@<vm-name>`. It must connect.

Then delete the three default Google Cloud rules that let the whole internet in. Go to VPC network, then
Firewall, and delete:

- `default-allow-ssh`
- `default-allow-rdp`
- `default-allow-icmp`

Leave `default-allow-internal`. After this, only Tailscale reaches the VM.

**If Tailscale breaks and you are locked out:** in the same Firewall page, add a rule that allows `tcp:22`
from **your current IP only** (search "what is my IP"), with a name like `temp-ssh`. Use the console's SSH
button, fix Tailscale, then **delete `temp-ssh`**.

## 3. Run the set-up script

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Allainnopro/pocket-assistant.git
sudo bash pocket-assistant/deploy/setup.sh
```

This downloads the code from the public GitHub repository and runs its script as root. It is your own
repository; look at `deploy/setup.sh` first if you want to see what it does.

It installs what is needed, creates the `pocket` user, the data folder `/var/lib/pocket-assistant/` (only
`pocket` can open it), a 1 GB swap file, the code in `/opt/pocket-assistant` and the service. You can run
it again safely. **It does not start the service.** (You can delete the `pocket-assistant` folder you
cloned in your home folder afterwards; the real copy is in `/opt`.)

Before the first start, the data folder needs `config.toml`, `profile.toml` and `assistant.db` (step 5 of
the move). In `config.toml`:

```toml
[storage]
db_path = "/var/lib/pocket-assistant/assistant.db"
[paths]
profile = "/var/lib/pocket-assistant/profile.toml"
[api]
tailnet_host = "<vm-name>.<your-tailnet>.ts.net"
tailnet_login = "you@example.com"   # your Tailscale login
```

`config.example.toml` shows where each goes. Files copied in must belong to `pocket`:


```bash
sudo chown -R pocket:pocket /var/lib/pocket-assistant
sudo chmod 700 /var/lib/pocket-assistant
sudo chmod 600 /var/lib/pocket-assistant/secrets.json   # only if it exists
```

## 6. Secrets on the VM

(Steps 4 and 5, stopping the engine on your PC and copying your files over, are in the main `README.md` and
in the move section of the design doc.)

Always run these as `pocket` (`sudo -u pocket`). Run as root, they would create a secrets file the service
cannot read. If you see a message about `chmod 600`, do what it says.

First create a **new** Gmail App Password for the VM at <https://myaccount.google.com/apppasswords>. Then:

```bash
sudo -u pocket /opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml set-key gmail
sudo -u pocket /opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml set-key anthropic
```

Paste each at the hidden prompt. Gmail is tested before it is saved. The keys are stored in
`/var/lib/pocket-assistant/secrets.json`.

To connect a device, show a pairing code (the same way, as `pocket`):

```bash
sudo -u pocket /opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml pair
```

Google may send a **security alert** at the first Gmail sign-in from the VM, because it is a US data
centre. Open the alert and confirm it was you.

## 7. Start it and check

```bash
sudo systemctl start pocket-assistant
sudo systemctl status pocket-assistant
sudo -u pocket /opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml auth
sudo tail -n 50 /var/lib/pocket-assistant/logs/engine.log
```

Then open `https://<vm-name>.<your-tailnet>.ts.net/api/today` from a device on your tailnet. To stop:
`sudo systemctl stop pocket-assistant` (a job in progress finishes first, up to 150 seconds).

## Updating

```bash
sudo bash /opt/pocket-assistant/deploy/update.sh
```

It pulls the newest code, installs it and restarts the service.

## Backups and restoring one

The engine saves a copy every night at 03:00 (Ho Chi Minh City time) to
`/var/lib/pocket-assistant/backups/assistant-YYYY-MM-DD.db.gz`, and keeps the newest 7. Your PC keeps its
own copies too.

To restore (this replaces the current database):

```bash
sudo gunzip -t /var/lib/pocket-assistant/backups/assistant-2026-11-26.db.gz && echo "backup is intact"
sudo systemctl stop pocket-assistant
sudo -u pocket cp /var/lib/pocket-assistant/assistant.db /var/lib/pocket-assistant/assistant.db.before-restore
sudo -u pocket sh -c 'gunzip -c /var/lib/pocket-assistant/backups/assistant-2026-11-26.db.gz > /var/lib/pocket-assistant/assistant.db'
sudo systemctl start pocket-assistant
```

Change the date to the backup you want (in both commands). Stop if the first command prints an error. To restore from a copy on your PC, copy the `.db.gz` into the
`backups` folder over Tailscale first (`chown pocket:pocket` it).

## After 48 hours

Open Billing, then Reports, and check the cost for the VM, its disk and its IP. Anything above $0 is
worth understanding before the month ends (the $1 alert should catch it). **Verify at set-up:** the free
tier's coverage of the external IP is not confirmed.

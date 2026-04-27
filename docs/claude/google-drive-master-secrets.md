# Google Drive master secrets workflow

How to fill, encrypt, verify, and use your master secrets file.

## Quick reference

| Step | What you do |
|---|---|
| 1 | Fill `master-secrets.yaml` in Google Drive (copy from `config/master-secrets.template.yaml`) |
| 2 | Run `notebooks/setup/encrypt_google_drive_master_secrets.ipynb` |
| 3 | Confirm `master-secrets.sops.yaml` and `age-keys.txt` exist in Drive |
| 4 | Delete plaintext `master-secrets.yaml` from Drive |
| 5 | Run `notebooks/setup/render_env_from_drive_master.ipynb` to create lean `.env` files |

---

## Folder layout

```
My Drive / ICT_Bot_Secrets /
├── ict-bot-master-secrets.template.yaml   ← template, safe to keep
├── master-secrets.yaml                    ← PLAINTEXT — delete after encrypting
├── master-secrets.sops.yaml               ← encrypted — safe to keep in Drive
├── age-keys.txt                           ← private key — keep, never commit
└── google-drive-master-secrets-setup.md  ← setup guide
```

---

## Step-by-step

### 1. Fill `master-secrets.yaml`

1. Open [Google Drive](https://drive.google.com) in your browser.
2. Go to **My Drive / ICT_Bot_Secrets**.
3. Make a copy of `config/master-secrets.template.yaml` from the repo (or `ict-bot-master-secrets.template.yaml` already in Drive).
4. Rename the copy to `master-secrets.yaml`.
5. Open it and fill in every `REPLACE_ME` value with real API keys and tokens.
6. Do not share the file or move it outside the `ICT_Bot_Secrets` folder.

### 2. Run the encryption notebook

Open in Colab:

```
notebooks/setup/encrypt_google_drive_master_secrets.ipynb
```

Run all cells top to bottom. The notebook:

- Mounts Google Drive.
- Verifies `master-secrets.yaml` is present.
- Installs SOPS and age.
- Creates an age key at `age-keys.txt` if one does not exist yet.
- Encrypts the file → `master-secrets.sops.yaml`.
- Runs a decryption test (output discarded, nothing printed).
- Asks you to delete the plaintext file.

### 3. Confirm the encrypted file

In Google Drive, confirm that `master-secrets.sops.yaml` and `age-keys.txt`
exist and are non-empty before deleting the plaintext file.

### 4. Delete `master-secrets.yaml`

1. Right-click `master-secrets.yaml` in Drive → **Move to Trash**.
2. Open the Trash → **Empty Trash**.

The encrypted file is the source of truth from this point on.

### 5. Generate lean `.env` files

Open in Colab:

```
notebooks/setup/render_env_from_drive_master.ipynb
```

Or run directly from a terminal (on the VM or locally with SOPS installed):

```bash
python scripts/render_env_from_master.py \
  --master /path/to/master-secrets.sops.yaml \
  --age-key-file /path/to/age-keys.txt \
  --profile paper \
  --out .env.paper
```

This produces the minimal `.env` files each service needs without exposing
unrelated keys.

---

## Example commands

### Paper (local)
```bash
python scripts/render_env_from_master.py \
  --master /content/drive/MyDrive/ICT_Bot_Secrets/master-secrets.sops.yaml \
  --age-key-file /content/drive/MyDrive/ICT_Bot_Secrets/age-keys.txt \
  --profile paper \
  --out .env.paper
```

### Colab
```bash
python scripts/render_env_from_master.py \
  --master /content/drive/MyDrive/ICT_Bot_Secrets/master-secrets.sops.yaml \
  --age-key-file /content/drive/MyDrive/ICT_Bot_Secrets/age-keys.txt \
  --profile colab \
  --out .env.colab
```

### Oracle VM (paper)
```bash
python scripts/render_env_from_master.py \
  --master ~/ICT_Bot_Secrets/master-secrets.sops.yaml \
  --age-key-file ~/ICT_Bot_Secrets/age-keys.txt \
  --profile oracle_paper \
  --out .env.oracle_paper
```

### Live (requires --allow-live)
```bash
python scripts/render_env_from_master.py \
  --master ~/ICT_Bot_Secrets/master-secrets.sops.yaml \
  --age-key-file ~/ICT_Bot_Secrets/age-keys.txt \
  --profile live \
  --out .env.live \
  --allow-live
```

---

## Secrets rules

- **Never commit** `master-secrets.yaml`, `age-keys.txt`, or any `.env*` file.
- `master-secrets.sops.yaml` is encrypted — keep it in Drive, do not commit to the repo.
- `config/master-secrets.template.yaml` is safe to commit — placeholder values only.
- Back up `age-keys.txt` in a password manager immediately after creating it.
- If you lose `age-keys.txt`, you cannot decrypt `master-secrets.sops.yaml`.
  Rotate all affected API keys and start the process again with a fresh key.
- Generated `.env.*` files are runtime artifacts — delete them from Colab after use.

---

## Re-encrypting after changing API keys

If you rotate or add API keys:

1. Create a new `master-secrets.yaml` in Drive with the updated values.
2. Run `notebooks/setup/encrypt_google_drive_master_secrets.ipynb` again.
3. The notebook overwrites the old `master-secrets.sops.yaml`.
4. Delete the new plaintext file.

You do not need to create a new age key.

---

## Using secrets on the Oracle VM

Copy `age-keys.txt` and `master-secrets.sops.yaml` to the VM once (via SCP):

```bash
scp -i ~/.ssh/id_rsa /path/to/age-keys.txt ubuntu@<VM_IP>:~/age-keys.txt
scp -i ~/.ssh/id_rsa /path/to/master-secrets.sops.yaml ubuntu@<VM_IP>:~/master-secrets.sops.yaml
chmod 600 ~/age-keys.txt ~/master-secrets.sops.yaml
```

Then generate the env file on the VM:

```bash
python scripts/render_env_from_master.py \
  --master ~/master-secrets.sops.yaml \
  --age-key-file ~/age-keys.txt \
  --profile oracle_paper \
  --out ~/ict-trading-bot/.env.oracle_paper
```

---

## Related files

| File | Purpose |
|---|---|
| `config/master-secrets.template.yaml` | Placeholder template — safe to commit |
| `scripts/render_env_from_master.py` | CLI script that decrypts and renders env files |
| `notebooks/setup/encrypt_google_drive_master_secrets.ipynb` | The encryption notebook |
| `notebooks/setup/render_env_from_drive_master.ipynb` | The rendering notebook |
| `docs/claude/security-secrets.md` | Secrets rules for Claude sessions |
| `docs/claude/deployment-ops.md` | VM deployment workflow |

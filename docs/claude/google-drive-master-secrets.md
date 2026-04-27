# Google Drive master secrets workflow

How to fill, encrypt, verify, and use your master secrets file.

## Quick reference

| Step | What you do |
|---|---|
| 1 | Fill `master-secrets.yaml` in Google Drive |
| 2 | Run the Colab encryption notebook |
| 3 | Confirm `master-secrets.sops.yaml` exists |
| 4 | Delete `master-secrets.yaml` from Drive |
| 5 | Use the repo script to generate lean `.env` files |

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
3. Make a copy of `ict-bot-master-secrets.template.yaml`.
4. Rename the copy to `master-secrets.yaml`.
5. Open it and fill in every API key and token with real values.
6. Do not share the file or move it outside the `ICT_Bot_Secrets` folder.

### 2. Run the encryption notebook

Open the notebook in Colab:

```
notebooks/setup/encrypt_google_drive_master_secrets.ipynb
```

Run all cells from top to bottom. The notebook:

- Mounts Google Drive.
- Verifies `master-secrets.yaml` is present.
- Installs SOPS and age (open-source encryption tools).
- Creates an age key at `age-keys.txt` if one does not exist yet.
- Encrypts the file → `master-secrets.sops.yaml`.
- Runs a decryption test (output discarded, nothing printed).
- Asks you to delete the plaintext file.

### 3. Confirm the encrypted file

In Google Drive, confirm that `master-secrets.sops.yaml` exists and is non-empty
before deleting the plaintext file.

### 4. Delete `master-secrets.yaml`

1. Right-click `master-secrets.yaml` in Drive → **Move to Trash**.
2. Open the Trash → **Empty Trash**.

The encrypted file is the source of truth from this point on.

### 5. Generate lean `.env` files

On the Oracle VM or in a Colab session:

```bash
export SOPS_AGE_KEY_FILE=~/age-keys.txt
sops --decrypt /path/to/master-secrets.sops.yaml | python scripts/generate_env.py
```

This produces the minimal `.env` files each service needs without exposing
unrelated keys.

---

## Secrets rules

- **Never commit** `master-secrets.yaml`, `age-keys.txt`, or any `.env` file.
- `master-secrets.sops.yaml` is encrypted and safe to commit to the repo.
- Back up `age-keys.txt` in a password manager immediately after creating it.
- If you lose `age-keys.txt`, you cannot decrypt `master-secrets.sops.yaml`.
  Rotate all affected API keys and start the process again with a fresh key.

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

Copy `age-keys.txt` to the VM once (via SCP or paste into a file):

```bash
scp -i ~/.ssh/id_rsa /path/to/age-keys.txt ubuntu@<VM_IP>:~/age-keys.txt
chmod 600 ~/age-keys.txt
```

Then decrypt on demand:

```bash
SOPS_AGE_KEY_FILE=~/age-keys.txt \
  sops --decrypt ~/ict-trading-bot/master-secrets.sops.yaml
```

Never leave decrypted output in a file. Pipe it directly to the script that needs it.

---

## Related files

| File | Purpose |
|---|---|
| `notebooks/setup/encrypt_google_drive_master_secrets.ipynb` | The encryption notebook |
| `docs/claude/security-secrets.md` | Secrets rules for Claude sessions |
| `docs/claude/deployment-ops.md` | VM deployment workflow |

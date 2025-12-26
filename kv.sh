# quick remove sample vault, and recreate it.
rm -rf ./dev-assets/vault-sample
cd dev-assets
venv/bin/python create-sample-vault.py

